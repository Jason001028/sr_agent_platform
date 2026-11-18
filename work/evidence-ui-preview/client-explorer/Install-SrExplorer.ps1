# Run on the Windows CLIENT, not the SR API server. No administrator required.
[CmdletBinding()]
param(
    [string]$ServerRoot = '/DiskArray',
    [string]$WindowsRoot = 'W:\',
    [string[]]$PickerOrigins = @('http://10.10.81.135'),
    [switch]$Uninstall,
    [switch]$Interactive
)
$ErrorActionPreference = 'Stop'
$installDir = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'SrAgentExplorer'
$protocolKey = 'HKCU:\Software\Classes\sr-explorer'
$handlerFile = Join-Path $installDir 'Open-SrDirectory.ps1'
$launcherFile = Join-Path $installDir 'Open-SrDirectory.exe'
$configFile = Join-Path $installDir 'mapping.json'
$pickerFile = Join-Path $installDir 'Pick-SrImage.ps1'
$chromePolicyScript = Join-Path $PSScriptRoot 'Chrome-SrExplorerPolicy.ps1'
if (-not (Test-Path -LiteralPath $chromePolicyScript)) {
    throw 'Extract the entire new setup ZIP first; Chrome-SrExplorerPolicy.ps1 must be beside the installer.'
}

if ($Uninstall) {
    & $chromePolicyScript -Remove
    if (Test-Path -LiteralPath $protocolKey) {
        $command = (Get-Item -LiteralPath "$protocolKey\shell\open\command").GetValue('')
        if (-not $command.Contains($handlerFile) -and -not $command.Contains($launcherFile)) { throw 'Protocol belongs to another installation; refusing to remove it.' }
        Remove-Item -LiteralPath $protocolKey -Recurse -Force
    }
    foreach ($file in @($handlerFile, $launcherFile, $configFile, $pickerFile)) {
        if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force }
    }
    if (Test-Path -LiteralPath $installDir) { Remove-Item -LiteralPath $installDir -ErrorAction SilentlyContinue }
    Write-Host 'SR Explorer protocol removed. Shift-click the folder icon to configure again.'
    exit
}
if ($Interactive) {
    $answer = Read-Host 'Mapped drive letter [W] - press Enter to keep W'
    if (-not [string]::IsNullOrWhiteSpace($answer)) {
        $answer = $answer.Trim()
        if ($answer -notmatch '^[A-Za-z](:[\\/]?)?$') { throw 'Enter one drive letter, e.g. W or Z.' }
        $WindowsRoot = $answer.Substring(0, 1) + ':\'
    }
}
if ($WindowsRoot -notmatch '^[A-Za-z]:[\\/]$') { throw 'WindowsRoot must be a mapped drive root, e.g. W:\ or Z:\.' }
$ServerRoot = $ServerRoot.Replace('\', '/').TrimEnd('/')
if ($ServerRoot -notmatch '^/(?!/)' -or $ServerRoot -match '[\x00-\x1f\x7f"<>|?*:]' -or
    ($ServerRoot.Split('/') | Where-Object { $_ -eq '.' -or $_ -eq '..' })) {
    throw 'ServerRoot must be an absolute POSIX directory without traversal, e.g. /DiskArray.'
}
if (-not $ServerRoot) { throw 'Mapping the entire server filesystem is not supported.' }
# 候选盘符：安装时指定的那个排最前，再补公司环境里常见的 W:、X:。
# 同一份安装包在盘阵挂 W: 和挂 X: 的机器上都能打开；handler 取第一个目标目录真实存在的。
$WindowsRoots = @($WindowsRoot, 'W:\', 'X:\') |
    ForEach-Object { $_.Substring(0, 1).ToUpperInvariant() + ':\' } |
    Select-Object -Unique
$handler = @'
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Uri, [switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
try {
    if ($args.Count -ne 0 -or $Uri.Length -gt 24000 -or $Uri -cnotmatch '^sr-explorer://(open|pick)/([A-Za-z0-9_-]+)$') {
        throw 'Invalid SR Explorer request.'
    }
    $action = $Matches[1]
    $encoded = $Matches[2].Replace('-', '+').Replace('_', '/')
    if (($encoded.Length % 4) -eq 1) { throw 'Invalid directory encoding.' }
    $encoded += '=' * ((4 - ($encoded.Length % 4)) % 4)
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $path = $utf8.GetString([Convert]::FromBase64String($encoded))
    if ($action -eq 'pick') {
        & (Join-Path $PSScriptRoot 'Pick-SrImage.ps1') -Callback $path -ValidateOnly:$ValidateOnly
        return
    }
    if ($path.Length -gt 4096 -or $path -notmatch '^/(?!/)' -or $path -match '[\x00-\x1f\x7f"<>|?*:\\]' -or
        ($path.Split('/') | Where-Object { $_ -eq '.' -or $_ -eq '..' -or $_ -match '[. ]$' })) {
        throw 'Invalid server directory.'
    }
    $mapping = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'mapping.json') -Raw | ConvertFrom-Json
    $root = [string]$mapping.server_root
    # windows_roots = 多个候选，按顺序取第一个目标存在的；旧配置只有 windows_root。
    $drives = @()
    if ($mapping.windows_roots) { $drives = @($mapping.windows_roots) }
    if ($drives.Count -eq 0) { $drives = @($mapping.windows_root) }
    $drives = @($drives | ForEach-Object { [string]$_ } | Where-Object { $_ -match '^[A-Z]:\\$' } | Select-Object -Unique)
    if (-not $root -or $drives.Count -eq 0) { throw 'Invalid client mapping. Please reinstall.' }
    if ($path -cne $root -and -not $path.StartsWith($root + '/', [StringComparison]::Ordinal)) {
        throw 'This directory is outside the configured server root. Reinstall with the correct ServerRoot.'
    }
    $relative = $path.Substring($root.Length).TrimStart('/').Replace('/', '\')
    # Windows treats these names as devices, even with a file extension.
    foreach ($part in $relative.Split('\')) {
        if ($part -match '^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\.|$)') { throw 'Reserved Windows path segment.' }
    }
    $target = $null
    foreach ($drive in $drives) {
        $candidate = [IO.Path]::GetFullPath($drive + $relative)
        if (-not $candidate.StartsWith($drive, [StringComparison]::OrdinalIgnoreCase)) { throw 'Directory escapes the configured drive.' }
        if ($ValidateOnly -or (Test-Path -LiteralPath $candidate -PathType Container)) { $target = $candidate; break }
    }
    if (-not $target) {
        $tried = ($drives | ForEach-Object { $_ + $relative }) -join '; '
        throw "The directory is unavailable ($tried). Check the mapped drive and network access on this computer."
    }
    if ($ValidateOnly) { Write-Output $target; return }
    # No shell / Invoke-Expression. Quotes and control characters were rejected.
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = Join-Path $env:WINDIR 'explorer.exe'
    $argumentPath = $target.TrimEnd('\') + '\.'
    $start.Arguments = '"' + $argumentPath + '"'
    $start.UseShellExecute = $false
    [Diagnostics.Process]::Start($start) | Out-Null
} catch {
    if ($ValidateOnly) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'SR Explorer') | Out-Null
    exit 1
}
'@
$launcherSource = @'
using System;
using System.Diagnostics;
using System.IO;
using System.Text.RegularExpressions;
using System.Windows.Forms;

public static class SrExplorerLauncher {
    [STAThread]
    public static int Main(string[] args) {
        try {
            if (args.Length != 1 || args[0].Length > 24000 ||
                !Regex.IsMatch(args[0], @"\Asr-explorer://(?:open|pick)/[A-Za-z0-9_-]+\z"))
                throw new ArgumentException("Invalid SR Explorer request.");
            string script = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Open-SrDirectory.ps1");
            if (!File.Exists(script)) throw new FileNotFoundException("Please reinstall SR Explorer.");
            var start = new ProcessStartInfo();
            start.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
                @"WindowsPowerShell\v1.0\powershell.exe");
            start.Arguments = "-STA -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" +
                script + "\" \"" + args[0] + "\"";
            // GUI-subsystem launcher + CREATE_NO_WINDOW: no console is created,
            // rather than hiding a console after PowerShell has already started.
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WindowStyle = ProcessWindowStyle.Hidden;
            using (Process child = Process.Start(start)) { }
            return 0;
        } catch (Exception error) {
            MessageBox.Show(error.Message, "SR Explorer", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
'@
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
# Build locally using the .NET Framework already used by Windows PowerShell.
# No downloaded EXE, external compiler package or administrator rights required.
$temporaryLauncher = Join-Path $installDir ('launcher-' + [guid]::NewGuid().ToString('N') + '.exe')
try {
    Add-Type -TypeDefinition $launcherSource -ReferencedAssemblies 'System.dll','System.Windows.Forms.dll' -OutputAssembly $temporaryLauncher -OutputType WindowsApplication
    [IO.File]::Copy($temporaryLauncher, $launcherFile, $true)
} finally {
    if (Test-Path -LiteralPath $temporaryLauncher) { Remove-Item -LiteralPath $temporaryLauncher -Force }
}
$handler | Set-Content -LiteralPath $handlerFile -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Pick-SrImage.ps1') -Destination $pickerFile -Force
@{ server_root=$ServerRoot; windows_root=$WindowsRoots[0]; windows_roots=@($WindowsRoots); picker_origins=$PickerOrigins } | ConvertTo-Json | Set-Content -LiteralPath $configFile -Encoding UTF8
New-Item -Path "$protocolKey\shell\open\command" -Force | Out-Null
Set-Item -LiteralPath $protocolKey -Value 'URL:SR Agent Explorer'
New-ItemProperty -LiteralPath $protocolKey -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
$command = '"' + $launcherFile + '" "%1"'
Set-Item -LiteralPath "$protocolKey\shell\open\command" -Value $command
Write-Host "Installed: $ServerRoot -> $($WindowsRoots -join ' | ') (first accessible wins)"
& $chromePolicyScript
Write-Host 'Return to http://10.10.81.135/viewer after checking Chrome policy status.'
if (-not ($WindowsRoots | Where-Object { Test-Path -LiteralPath $_ -PathType Container })) {
    Write-Warning 'None of the candidate drives is currently accessible. Connect one before opening a directory.'
}
