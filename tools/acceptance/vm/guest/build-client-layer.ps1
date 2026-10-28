# Build the "agent clients" layer inside a Windows test VM (runs as the
# interactive test user in session 1, elevated, via a scheduled task).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File build-client-layer.ps1 -Base http://10.211.55.2:<port> -Out C:\skilltest\build
#
# Installers are pinned and served by the host (tools/acceptance/vm/build_layer.py);
# every file is checked against the SHA256 in <Base>/manifest.json before use.
# No credentials are installed: the last step fails the build if any client
# login state or token-looking environment variable exists.
# Python: with a `python_exe` file in the manifest the python.org installer is
# used (per-user, "Add python.exe to PATH" ticked, py launcher included) and the
# install manager plus its runtimes are removed first, so `python` and `py -3`
# both resolve to that one version; otherwise the install manager installs
# `python_version` (or its current default when unset).
# manifest.steps (optional) limits the run to those steps, e.g. a Python-only
# variant on top of an existing client layer: ["python","verify","zero-credentials"].
# Result: <Out>\result.json (the only thing the host trusts). ASCII-only file:
# Windows PowerShell 5.1 reads BOM-less scripts in the ANSI code page.
param(
    [Parameter(Mandatory = $true)][string]$Base,
    [Parameter(Mandatory = $true)][string]$Out
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
New-Item -ItemType Directory -Force -Path $Out, (Join-Path $Out 'dl') | Out-Null
$logPath = Join-Path $Out 'build.log'
$res = [ordered]@{
    schema   = 'agent-skill-acceptance/client-layer@1'
    ok       = $false
    session  = (Get-Process -Id $PID).SessionId
    user     = "$env:USERDOMAIN\$env:USERNAME"
    steps    = @()
    versions = [ordered]@{}
    which    = [ordered]@{}
    error    = $null
}

function Log([string]$m) {
    ('[{0}] {1}' -f (Get-Date).ToUniversalTime().ToString('o'), $m) | Out-File -Append -Encoding utf8 -FilePath $logPath
}

function Step([string]$name, [scriptblock]$body) {
    if ($script:only -and ($script:only -notcontains $name)) { Log "skip $name"; return }
    Log "step $name"
    $t = Get-Date
    & $body
    $script:res.steps += [ordered]@{ name = $name; seconds = [int]((Get-Date) - $t).TotalSeconds }
}

function Refresh-Path {
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$m;$u"
}

function Run-Exe([string]$file, [string]$argline, [int]$timeoutSec = 1800) {
    Log "run $file $argline"
    $p = Start-Process -FilePath $file -ArgumentList $argline -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Out 'last.out') -RedirectStandardError (Join-Path $Out 'last.err')
    $null = $p.Handle  # cache the handle, otherwise ExitCode stays $null
    if (-not $p.WaitForExit($timeoutSec * 1000)) { $p.Kill(); throw "timeout: $file" }
    Get-Content -Raw -ErrorAction SilentlyContinue (Join-Path $Out 'last.out') | Out-File -Append -Encoding utf8 $logPath
    Get-Content -Raw -ErrorAction SilentlyContinue (Join-Path $Out 'last.err') | Out-File -Append -Encoding utf8 $logPath
    if ($p.ExitCode -ne 0) { throw "exit $($p.ExitCode): $file $argline" }
}

function Capture([string]$file, [string]$argline) {
    # First line of stdout+stderr of a short command, '' on failure.
    try {
        $o = & cmd.exe /d /c "`"$file`" $argline 2>&1"
        return (($o | Out-String).Trim() -split "`r?`n")[0]
    } catch { return '' }
}

try {
    $manifest = Invoke-RestMethod -UseBasicParsing -Uri "$Base/manifest.json"
    $files = @{}
    Step 'download' {
        foreach ($f in $manifest.files) {
            $dst = Join-Path (Join-Path $Out 'dl') $f.name
            Invoke-WebRequest -UseBasicParsing -Uri "$Base/$($f.name)" -OutFile $dst
            $h = (Get-FileHash -Algorithm SHA256 -Path $dst).Hash.ToLower()
            $len = (Get-Item $dst).Length
            if ($h -ne $f.sha256 -or $len -ne [int64]$f.size) { throw "checksum/size mismatch: $($f.name)" }
            $files[$f.role] = $dst
        }
    }

    $script:only = @($manifest.steps | Where-Object { $_ })
    $pyExpect = [string]$manifest.python_version
    if ($files.ContainsKey('python_exe')) {
        Step 'python' {
            # A buyer who installed Python from python.org: drop the install
            # manager (its py.exe alias would shadow the classic launcher).
            $pyMgr = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\py.exe'
            if (Test-Path $pyMgr) {
                foreach ($tag in @($manifest.python_uninstall)) {
                    if ($tag) { try { Run-Exe $pyMgr "uninstall --yes $tag" 600 } catch { Log "uninstall $tag : $($_.Exception.Message)" } }
                }
            }
            Get-AppxPackage -Name 'PythonSoftwareFoundation.PythonManager*' | Remove-AppxPackage
            $lp = Join-Path $env:LOCALAPPDATA 'Python'
            if (Test-Path $lp) { Remove-Item -Recurse -Force $lp }
            $up = [Environment]::GetEnvironmentVariable('Path', 'User')
            $keep = @($up -split ';' | Where-Object { $_ -and $_ -notmatch '\\Python\\(bin|pythoncore)' })
            [Environment]::SetEnvironmentVariable('Path', ($keep -join ';'), 'User')
            Run-Exe $files['python_exe'] '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 InstallLauncherAllUsers=0 Include_test=0 Shortcuts=1' 1800
            Refresh-Path
        }
    } else {
    Step 'python' {
        Add-AppxPackage -Path $files['python_manager']
        Refresh-Path
        $py = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\py.exe'
        for ($i = 0; $i -lt 30 -and -not (Test-Path $py); $i++) { Start-Sleep 1 }
        if (-not (Test-Path $py)) { throw 'py.exe alias not registered after Add-AppxPackage' }
        # Same path the official installer page sends a buyer down: the install
        # manager, then its first-run configuration, which installs the current
        # default CPython and adds the global shortcuts dir to the user PATH.
        Run-Exe $py "install --configure --yes" 1800
        if ($pyExpect) { Run-Exe $py "install --yes $pyExpect" 1800 }
        # Runtimes left behind by an earlier (re)build attempt, if any.
        foreach ($tag in @($manifest.python_uninstall)) {
            if ($tag) { try { Run-Exe $py "uninstall --yes $tag" 600 } catch { Log "uninstall $tag : $($_.Exception.Message)" } }
        }
        Refresh-Path
    }
    }

    Step 'node' {
        Run-Exe 'msiexec.exe' "/i `"$($files['node_msi'])`" /qn /norestart /l*v `"$(Join-Path $Out 'node-msi.log')`""
        Refresh-Path
    }

    Step 'git' {
        Run-Exe $files['git_exe'] '/VERYSILENT /NORESTART /NOCANCEL /SP- /SUPPRESSMSGBOXES /COMPONENTS="gitlfs,assoc,assoc_sh"'
        Refresh-Path
    }

    Step 'codex' {
        $npm = Join-Path $env:ProgramFiles 'nodejs\npm.cmd'
        Run-Exe 'cmd.exe' "/d /c `"`"$npm`" install -g --no-fund --no-audit $($manifest.codex_npm)`""
        Refresh-Path
    }

    Step 'claude' {
        # Native installer: the pinned binary installs itself as the launcher.
        Run-Exe $files['claude_exe'] "install $($manifest.claude_version)" 900
        # The installer only prints a note when its bin dir is not on PATH; the
        # documented next step for a user is adding it, so do that.
        $bin = Join-Path $env:USERPROFILE '.local\bin'
        $up = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (($up -split ';') -notcontains $bin) { [Environment]::SetEnvironmentVariable('Path', ($up.TrimEnd(';') + ';' + $bin), 'User') }
        [Environment]::SetEnvironmentVariable('DISABLE_UPDATES', '1', 'User')
        [Environment]::SetEnvironmentVariable('DISABLE_AUTOUPDATER', '1', 'User')
        Refresh-Path
    }

    Step 'verify' {
        Refresh-Path
        foreach ($n in 'python', 'py', 'node', 'npm', 'git', 'codex', 'claude') {
            $c = Get-Command $n -ErrorAction SilentlyContinue | Select-Object -First 1
            $res.which[$n] = if ($c) { $c.Source } else { $null }
        }
        $res.versions['python'] = Capture 'python' '--version'
        $res.versions['py3'] = Capture 'py' '-3 --version'
        $res.versions['python3'] = Capture 'python3' '--version'
        $res.versions['node'] = Capture 'node' '--version'
        $res.versions['git'] = Capture 'git' '--version'
        $res.versions['codex'] = Capture 'codex' '--version'
        $res.versions['claude'] = Capture 'claude' '--version'
        $res.versions['windows'] = (Get-CimInstance Win32_OperatingSystem).Caption + ' ' + (Get-CimInstance Win32_OperatingSystem).Version
        $ubr = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').UBR
        $res.versions['windows_build'] = "$((Get-CimInstance Win32_OperatingSystem).BuildNumber).$ubr"
        $res.versions['edge'] = (Get-Item -ErrorAction SilentlyContinue "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe").VersionInfo.ProductVersion
        # `python` may still hit the Microsoft Store stub alias (the install
        # manager tells the user to toggle app execution aliases by hand); the
        # skills fall back to `py -3`, so require that one and record the rest.
        foreach ($k in 'py3', 'node', 'git', 'codex', 'claude') {
            if (-not $res.versions[$k] -or $res.versions[$k] -notmatch '\d+\.\d+') { throw "client check failed: $k -> '$($res.versions[$k])'" }
        }
        $res.versions['ansi_codepage'] = [string](Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage').ACP
        $res.versions['pythonutf8'] = [string]([Environment]::GetEnvironmentVariable('PYTHONUTF8', 'User') + [Environment]::GetEnvironmentVariable('PYTHONUTF8', 'Machine'))
        if ($pyExpect) {
            # The pinned version must be what both entry points resolve to.
            $want = 'Python ' + $pyExpect
            if ($res.versions['py3'] -ne $want -and -not $res.versions['py3'].StartsWith($want + '.')) { throw "py -3 is '$($res.versions['py3'])', want $want" }
            if ($files.ContainsKey('python_exe') -and $res.versions['python'] -ne $want -and -not $res.versions['python'].StartsWith($want + '.')) { throw "python is '$($res.versions['python'])', want $want" }
        }
    }

    Step 'zero-credentials' {
        $bad = @()
        foreach ($p in "$env:USERPROFILE\.codex\auth.json", "$env:USERPROFILE\.claude\.credentials.json",
                       "$env:USERPROFILE\.codebuddy\auth.json") {
            if (Test-Path $p) { $bad += $p }
        }
        foreach ($scope in 'User', 'Machine') {
            $vars = [Environment]::GetEnvironmentVariables($scope)
            foreach ($k in $vars.Keys) {
                if ($k -match '(TOKEN|API_KEY|SECRET|PASSWORD)') { $bad += "env:$scope/$k" }
            }
        }
        if ($bad.Count) { throw "credential material present: $($bad -join ', ')" }
    }

    Remove-Item -Recurse -Force (Join-Path $Out 'dl') -ErrorAction SilentlyContinue
    $res.ok = $true
} catch {
    $res.error = "$($_.Exception.Message)`n$($_.ScriptStackTrace)"
    Log "ERROR $($res.error)"
} finally {
    $res | ConvertTo-Json -Depth 5 | Out-File -Encoding utf8 -FilePath (Join-Path $Out 'result.json')
    Log 'done'
}
