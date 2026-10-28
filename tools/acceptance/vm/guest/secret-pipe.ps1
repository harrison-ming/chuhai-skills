# Hand acceptance credentials to the round task without touching disk or a
# command line. Runs as SYSTEM via `prlctl exec ... -File secret-pipe.ps1`;
# the host writes one line of base64(JSON) to stdin. The value is served ONCE
# over a named pipe whose ACL admits only the test user, then this process
# exits. Never prints the value. ASCII-only file (PowerShell 5.1).
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$User,
    [int]$TimeoutSec = 600
)
$ErrorActionPreference = 'Stop'
try {
    $payload = [Console]::In.ReadLine()
    if (-not $payload -or $payload.Length -lt 8) { '{"ok":false,"err":"empty stdin"}'; exit 2 }
    $sec = New-Object System.IO.Pipes.PipeSecurity
    $sec.AddAccessRule((New-Object System.IO.Pipes.PipeAccessRule($User, 'ReadWrite', 'Allow')))
    $sec.AddAccessRule((New-Object System.IO.Pipes.PipeAccessRule('NT AUTHORITY\SYSTEM', 'FullControl', 'Allow')))
    $srv = New-Object System.IO.Pipes.NamedPipeServerStream($Name, [System.IO.Pipes.PipeDirection]::Out, 1,
        [System.IO.Pipes.PipeTransmissionMode]::Byte, [System.IO.Pipes.PipeOptions]::Asynchronous, 0, 0, $sec)
    $ar = $srv.BeginWaitForConnection($null, $null)
    if (-not $ar.AsyncWaitHandle.WaitOne($TimeoutSec * 1000)) { $srv.Dispose(); '{"ok":false,"err":"no client"}'; exit 3 }
    $srv.EndWaitForConnection($ar)
    $bytes = [Text.Encoding]::ASCII.GetBytes($payload + "`n")
    $srv.Write($bytes, 0, $bytes.Length)
    $srv.Flush()
    $srv.WaitForPipeDrain()
    $srv.Dispose()
    $payload = $null
    '{"ok":true}'
} catch {
    '{"ok":false,"err":"' + ($_.Exception.GetType().Name) + '"}'
    exit 1
}
