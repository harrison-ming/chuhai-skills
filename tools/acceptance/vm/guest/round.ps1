# One acceptance round inside a Windows test VM. Runs as the interactive test
# user (session 1) from a scheduled task registered by tools/acceptance/vm/run_vm.py.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File round.ps1 -Dir C:\skilltest\round
#
# Reads <Dir>\plan.json (UTF-8), installs the delivered skill zip per case,
# runs the case (script steps or a headless agent client), then PUTs the
# evidence to the host. The LAST upload is _round_done.json; the host only
# trusts what it received. Credentials (agent runs only) arrive over a named
# pipe (secret-pipe.ps1), live in this process's memory and in the child
# process environment, and are never written to logs. Codex needs auth.json
# on disk: it goes to <Dir>\codex-home (ACL: test user + SYSTEM only), and the
# host reads it back and deletes it after the round.
# ASCII-only file: Windows PowerShell 5.1 reads BOM-less scripts as ANSI.
param([Parameter(Mandatory = $true)][string]$Dir)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$logPath = Join-Path $Dir 'round.log'

function Log([string]$m) {
    ('[{0}] {1}' -f [DateTime]::UtcNow.ToString('o'), $m) | Out-File -Append -Encoding utf8 -FilePath $logPath
}
function UtcNow { [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ') }

function Quote-Arg([string]$a) {
    # CommandLineToArgvW-compatible quoting (same rules as subprocess.list2cmdline).
    if ($a.Length -gt 0 -and $a -notmatch '[\s"]') { return $a }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('"')
    $bs = 0
    foreach ($ch in $a.ToCharArray()) {
        if ($ch -eq [char]'\') { $bs++; continue }
        if ($ch -eq [char]'"') { [void]$sb.Append('\' * ($bs * 2 + 1)); [void]$sb.Append('"'); $bs = 0; continue }
        if ($bs) { [void]$sb.Append('\' * $bs); $bs = 0 }
        [void]$sb.Append($ch)
    }
    if ($bs) { [void]$sb.Append('\' * ($bs * 2)) }
    [void]$sb.Append('"')
    return $sb.ToString()
}

function Invoke-Argv([string]$exe, [string[]]$argv, [hashtable]$envAdd, [string]$cwd,
                     [string]$outPath, [string]$errPath, [int]$timeoutSec) {
    # Raw stdout/stderr bytes go to files (no re-encoding), stdin is closed.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = (($argv | ForEach-Object { Quote-Arg $_ }) -join ' ')
    $psi.WorkingDirectory = $cwd
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    foreach ($k in $envAdd.Keys) { $psi.EnvironmentVariables[$k] = [string]$envAdd[$k] }
    $t0 = Get-Date
    $p = [System.Diagnostics.Process]::Start($psi)
    $p.StandardInput.Close()
    $fo = [IO.File]::Open($outPath, 'Create')
    $fe = [IO.File]::Open($errPath, 'Create')
    $to = $p.StandardOutput.BaseStream.CopyToAsync($fo)
    $te = $p.StandardError.BaseStream.CopyToAsync($fe)
    $timedOut = $false
    if (-not $p.WaitForExit($timeoutSec * 1000)) {
        $timedOut = $true
        & taskkill.exe /T /F /PID $p.Id 2>&1 | Out-Null
        $p.WaitForExit(30000) | Out-Null
    }
    try { [void]$to.Wait(30000); [void]$te.Wait(30000) } catch { }
    $fo.Close(); $fe.Close()
    $code = $null
    try { $code = $p.ExitCode } catch { }
    return [ordered]@{ exit_code = $code; timed_out = $timedOut; duration_s = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1) }
}

function Capture-First([string]$exe, [string[]]$argv) {
    try {
        $o = Join-Path $Dir 'cap.out'; $e = Join-Path $Dir 'cap.err'
        $r = Invoke-Argv $exe $argv @{} $Dir $o $e 60
        $txt = ([IO.File]::ReadAllText($o) + [IO.File]::ReadAllText($e)).Trim()
        return [ordered]@{ ok = ($r.exit_code -eq 0); text = (($txt -split "`r?`n")[0]) }
    } catch { return [ordered]@{ ok = $false; text = $_.Exception.Message } }
}

function Resolve-Python {
    # Same order a skill's step 0 tells the agent to try: python, python3, py -3.
    foreach ($cand in @(@('python'), @('python3'), @('py', '-3'))) {
        $c = Get-Command $cand[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $c) { continue }
        $rest = @(); if ($cand.Count -gt 1) { $rest = $cand[1..($cand.Count - 1)] }
        $v = Capture-First $c.Source ($rest + @('--version'))
        if ($v.ok -and $v.text -match '^Python 3\.(\d+)' -and [int]$Matches[1] -ge 8) {
            return [ordered]@{ exe = $c.Source; pre = $rest; label = ($cand -join ' '); version = $v.text }
        }
    }
    return $null
}

function Put-File([string]$path, [string]$rel) {
    $segs = ($rel -replace '\\', '/').Split('/') | ForEach-Object { [Uri]::EscapeDataString($_) }
    $url = $plan.put_base + '/' + ($segs -join '/')
    for ($i = 0; $i -lt 3; $i++) {
        try {
            if ((Get-Item -LiteralPath $path).Length -eq 0) {
                Invoke-WebRequest -UseBasicParsing -Method Put -Uri $url -Body ([byte[]]@()) | Out-Null
            } else {
                Invoke-WebRequest -UseBasicParsing -Method Put -Uri $url -InFile $path | Out-Null
            }
            return $true
        } catch { Log "upload retry $rel : $($_.Exception.Message)"; Start-Sleep -Seconds (2 * ($i + 1)) }
    }
    return $false
}

function Put-Json($obj, [string]$rel) {
    $tmp = Join-Path $Dir ('put-' + [guid]::NewGuid().ToString('N') + '.json')
    [IO.File]::WriteAllText($tmp, ($obj | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
    $ok = Put-File $tmp $rel
    Remove-Item -Force $tmp
    return $ok
}

function Upload-Tree([string]$root, [string]$prefix, [string[]]$skipTop) {
    $n = 0; $failed = 0
    Get-ChildItem -LiteralPath $root -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $rel = $_.FullName.Substring($root.Length).TrimStart('\')
        $top = ($rel -split '\\')[0]
        if ($skipTop -contains $top) { return }
        if (Put-File $_.FullName "$prefix/$rel") { $n++ } else { $failed++ }
    }
    return @($n, $failed)
}

function Restrict-Acl([string]$path) {
    & icacls.exe $path /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls failed on $path ($LASTEXITCODE)" }
}

$roundRes = [ordered]@{ schema = 'agent-skill-acceptance/guest-round@1'; ok = $false; started_at = (UtcNow); cases = @(); error = $null }
try {
    Log "start session=$((Get-Process -Id $PID).SessionId) user=$env:USERNAME"
    $plan = [IO.File]::ReadAllText((Join-Path $Dir 'plan.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json

    # Fresh PATH from the registry (a scheduled task may carry a stale one).
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')

    $zip = Join-Path $Dir $plan.zip.name
    $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLower()
    if ($h -ne $plan.zip.sha256) { throw "zip sha256 mismatch in guest" }

    # ---------------------------------------------------------------- env
    $py = Resolve-Python
    $envInfo = [ordered]@{
        python = $(if ($py) { $py.version } else { $null })
        python_cmd = $(if ($py) { $py.label } else { $null })
        python_exe = $(if ($py) { $py.exe } else { $null })
        os_caption = (Get-CimInstance Win32_OperatingSystem).Caption
        os_build = "$((Get-CimInstance Win32_OperatingSystem).BuildNumber).$((Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').UBR)"
        arch = $env:PROCESSOR_ARCHITECTURE
        ui_culture = (Get-UICulture).Name
        ansi_codepage = [Text.Encoding]::Default.CodePage
        oem_codepage = [Console]::OutputEncoding.CodePage
        user = $env:USERNAME
        session = (Get-Process -Id $PID).SessionId
        clients = [ordered]@{}
    }
    $codexJs = Join-Path $env:APPDATA 'npm\node_modules\@openai\codex\bin\codex.js'
    $nodeExe = (Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    $claudeExe = (Get-Command claude -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if ($nodeExe -and (Test-Path $codexJs)) { $envInfo.clients['codex-cli'] = (Capture-First $nodeExe @($codexJs, '--version')).text }
    if ($claudeExe) { $envInfo.clients['claude-cli'] = (Capture-First $claudeExe @('--version')).text }
    Put-Json $envInfo 'env.json' | Out-Null

    # ---------------------------------------------------------------- secrets
    $secretEnv = @{}
    $codexHome = $null
    if ($plan.secret_pipe) {
        $cl = New-Object System.IO.Pipes.NamedPipeClientStream('.', $plan.secret_pipe, [System.IO.Pipes.PipeDirection]::In)
        $cl.Connect(120000)
        $sr = New-Object System.IO.StreamReader($cl, [Text.Encoding]::ASCII)
        $b64 = $sr.ReadLine()
        $sr.Dispose()
        $sec = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64)) | ConvertFrom-Json
        $b64 = $null
        if ($sec.env) { foreach ($p in $sec.env.PSObject.Properties) { $secretEnv[$p.Name] = [string]$p.Value } }
        if ($sec.codex_auth_b64) {
            $codexHome = Join-Path $Dir 'codex-home'
            New-Item -ItemType Directory -Force -Path $codexHome | Out-Null
            Restrict-Acl $codexHome
            [IO.File]::WriteAllBytes((Join-Path $codexHome 'auth.json'), [Convert]::FromBase64String($sec.codex_auth_b64))
        }
        $sec = $null
        Log "credentials received: env=$($secretEnv.Keys.Count) codex_auth=$([bool]$codexHome)"
    }

    # ---------------------------------------------------------------- cases
    foreach ($case in $plan.cases) {
        $client = $case.client
        $work = Join-Path $plan.guest_root ($client + '-' + $case.id)
        $meta = [ordered]@{ case_id = $case.id; client = $client; started_at = (UtcNow); steps = @(); install_ok = $false; error = $null }
        try {
            if (Test-Path -LiteralPath $work) { Remove-Item -Recurse -Force -LiteralPath $work }
            $deliv = Join-Path $work 'deliverables'
            New-Item -ItemType Directory -Force -Path $deliv | Out-Null
            $skillParent = switch ($client) {
                'script' { Join-Path $work 'skill' }
                'codex-cli' { Join-Path $work '.agents\skills' }
                'claude-cli' { Join-Path $work '.claude\skills' }
                default { throw "unknown client $client" }
            }
            New-Item -ItemType Directory -Force -Path $skillParent | Out-Null
            Expand-Archive -LiteralPath $zip -DestinationPath $skillParent -Force
            $skillDir = Join-Path $skillParent $plan.skill
            if (-not (Test-Path -LiteralPath (Join-Path $skillDir 'SKILL.md'))) { throw 'SKILL.md missing after unzip' }
            $meta.install_ok = $true
            $baseEnv = @{ CHUHAI_OUTPUT_DIR = $deliv; CHUHAI_NO_OPEN = '1' }

            if ($client -eq 'script') {
                if (-not $py) { throw 'no Python 3.8+ found (python / python3 / py -3)' }
                $i = 0
                foreach ($st in $case.steps) {
                    $argv = @()
                    foreach ($a in $st.argv) { $argv += [string]$a }
                    $exe = $null
                    if ($argv[0] -eq '{python}') { $exe = $py.exe; $argv = @($py.pre) + @($argv | Select-Object -Skip 1) }
                    else { $exe = $argv[0]; $argv = @($argv | Select-Object -Skip 1) }
                    $newest = Get-ChildItem -LiteralPath $deliv -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
                    $resolved = @()
                    foreach ($a in $argv) {
                        $a = $a.Replace('{skill_dir}', $skillDir).Replace('{out_root}', $deliv).Replace('{work_dir}', $work)
                        if ($a.Contains('{first_out_subdir}')) {
                            if (-not $newest) { throw "step $i needs {first_out_subdir} but $deliv is empty" }
                            $a = $a.Replace('{first_out_subdir}', $newest.FullName)
                        }
                        $resolved += $a
                    }
                    $r = Invoke-Argv $exe $resolved ($baseEnv + @{ PYTHONDONTWRITEBYTECODE = '1' }) $work `
                        (Join-Path $work "step$i.out") (Join-Path $work "step$i.err") ([int]$case.timeout)
                    $r['argv'] = @($exe) + $resolved
                    $meta.steps += $r
                    $i++
                }
            } else {
                $exe = $null; $argv = @()
                foreach ($a in $case.argv) { $argv += [string]$a }
                $envAll = $baseEnv.Clone()
                foreach ($k in $secretEnv.Keys) { $envAll[$k] = $secretEnv[$k] }
                if ($client -eq 'codex-cli') {
                    if (-not $codexHome) { throw 'codex auth not delivered' }
                    $exe = $nodeExe; $argv = @($codexJs) + $argv
                    $envAll['CODEX_HOME'] = $codexHome
                } else {
                    $exe = $claudeExe
                    $envAll['DISABLE_UPDATES'] = '1'
                }
                if (-not $exe) { throw "client executable not found for $client" }
                $r = Invoke-Argv $exe $argv $envAll $work (Join-Path $work 'transcript.jsonl') (Join-Path $work 'stderr.txt') ([int]$case.timeout)
                $r['argv_redacted'] = @($client) + @($argv | ForEach-Object { if ($_.Length -gt 300) { $_.Substring(0, 300) + '...' } else { $_ } })
                $meta.steps += $r
                $envAll = $null
            }
        } catch {
            $meta.error = "$($_.Exception.Message)`n$($_.ScriptStackTrace)"
            Log "case $($case.id) error: $($meta.error)"
        }
        $meta.ended_at = (UtcNow)
        $up = @(0, 0)
        if (Test-Path -LiteralPath $work) {
            $up = Upload-Tree $work $case.upload_prefix @('skill', '.agents', '.claude', '.codex')
        }
        $meta.uploaded = $up[0]; $meta.upload_failed = $up[1]
        Put-Json $meta "$($case.upload_prefix)/case-meta.json" | Out-Null
        $roundRes.cases += [ordered]@{ case_id = $case.id; client = $client; error = $meta.error }
    }
    $secretEnv = $null
    $roundRes.ok = $true
} catch {
    $roundRes.error = "$($_.Exception.Message)`n$($_.ScriptStackTrace)"
    Log "round error: $($roundRes.error)"
} finally {
    $roundRes.ended_at = (UtcNow)
    try { Put-File $logPath 'round.log' | Out-Null } catch { }
    try { Put-Json $roundRes '_round_done.json' | Out-Null } catch { Log 'final upload failed' }
    $roundRes | ConvertTo-Json -Depth 6 | Out-File -Encoding utf8 (Join-Path $Dir 'round-result.json')
}
