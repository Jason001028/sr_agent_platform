# Compile and exercise the GUI launcher with a harmless recording handler.
# Does not register any protocol, modify browser policy, or launch Explorer.
$ErrorActionPreference = 'Stop'
$source = [IO.File]::ReadAllText((Join-Path $PSScriptRoot '..\frontend\public\client-explorer\Install-SrExplorer.ps1'))
$pattern = "(?s)\`$launcherSource = @'\r?\n(.*?)\r?\n'@"
$match = [regex]::Match($source, $pattern)
if (-not $match.Success) { throw 'Launcher source not found.' }
$testDir = Join-Path $PSScriptRoot ('fixtures\launcher space-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDir -Force | Out-Null
$exe = Join-Path $testDir 'Open-SrDirectory.exe'
$handler = Join-Path $testDir 'Open-SrDirectory.ps1'
$record = Join-Path $testDir 'record.json'
try {
    Add-Type -TypeDefinition $match.Groups[1].Value -ReferencedAssemblies 'System.dll','System.Windows.Forms.dll' -OutputAssembly $exe -OutputType WindowsApplication
    $bytes = [IO.File]::ReadAllBytes($exe)
    $peOffset = [BitConverter]::ToInt32($bytes, 0x3c)
    $subsystem = [BitConverter]::ToUInt16($bytes, $peOffset + 24 + 68)
    if ($subsystem -ne 2) { throw 'Launcher is not a Windows GUI application.' }
    $stub = @'
param([string]$Uri)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class ConsoleProbe { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'
@{ uri=$Uri; console_window=[ConsoleProbe]::GetConsoleWindow().ToInt64() } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'record.json') -Encoding UTF8
'@
    $stub | Set-Content -LiteralPath $handler -Encoding UTF8
    $uri = 'sr-explorer://open/L0Rpc2tBcnJheS9zY2VuZQ'
    $process = Start-Process -FilePath $exe -ArgumentList $uri -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(10000) -or $process.ExitCode -ne 0) { throw 'Launcher failed.' }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path -LiteralPath $record) -and $clock.Elapsed.TotalSeconds -lt 15) { Start-Sleep -Milliseconds 100 }
    if (-not (Test-Path -LiteralPath $record)) { throw 'Hidden handler did not run.' }
    # The child writes asynchronously; wait until the complete JSON is readable.
    $result = $null
    while ($clock.Elapsed.TotalSeconds -lt 15) {
        try { $result = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json; if ($result) { break } } catch { }
        Start-Sleep -Milliseconds 50
    }
    if (-not $result -or $result.uri -cne $uri -or $result.console_window -ne 0) { throw 'Handler arguments or hidden-console invariant failed.' }
    Write-Output 'PASS: Windows GUI subsystem, path with spaces, exact URI, PowerShell console HWND=0; no registry or Explorer changes.'
} finally {
    if ($process) { $process.Dispose() }
    Remove-Item -LiteralPath $exe, $handler, $record -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $testDir
}
