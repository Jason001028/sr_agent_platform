[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Callback,[switch]$ValidateOnly)
$ErrorActionPreference='Stop'
[AppContext]::SetSwitch('Switch.System.IO.UseLegacyPathHandling',$false)
[AppContext]::SetSwitch('Switch.System.IO.BlockLongPaths',$false)
$target=[Uri]$Callback
$mapping=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'mapping.json') -Raw | ConvertFrom-Json
$origin=$target.GetLeftPart([UriPartial]::Authority).TrimEnd('/')
$allowed=@($mapping.picker_origins | ForEach-Object { ([Uri]$_).GetLeftPart([UriPartial]::Authority).TrimEnd('/') })
if ($target.Scheme -notin @('http','https') -or $origin -notin $allowed -or $target.UserInfo -or $target.Query -or $target.Fragment -or
    $target.AbsolutePath -cnotmatch '^/api/image-open/tickets/[a-f0-9]{64}/selection$') { throw 'Invalid picker callback. Reinstall with the correct PickerOrigins.' }
if ($ValidateOnly) { Write-Output $Callback; return }
$base=$Callback.Substring(0,$Callback.Length-10)
function Send-Json([string]$Url,$Value) {
    $json=$Value | ConvertTo-Json -Depth 12 -Compress
    Invoke-RestMethod -Uri $Url -Method Post -Body ([Text.Encoding]::UTF8.GetBytes($json)) -ContentType 'application/json' -TimeoutSec 120 -MaximumRedirection 0
}
function Wait-Ready {
    do {
        $control=Invoke-RestMethod -Uri ($base+'?brief=true') -TimeoutSec 30 -MaximumRedirection 0
        if($control.state -in @('cancelled','error')){return $false}
        if($control.state -eq 'paused'){Start-Sleep -Milliseconds 500}
    }while($control.state -eq 'paused')
    return $true
}
function Get-SrImageSamples([IO.Stream]$Stream) {
    # Both arguments must be Int64: PowerShell otherwise selects Max/Min(Int32,
    # Int32) from the literal first argument, overflowing before Seek is called.
    [long]$length=$Stream.Length
    [long[]]$offsets=@([long]0,
        [Math]::Max([long]0,[long][Math]::Floor(($length-[long]4096)/2.0)),
        [Math]::Max([long]0,($length-[long]4096)))
    [int]$sampleLength=[Math]::Min([long]4096,$length)
    $samples=@()
    foreach ($offset in $offsets) {
        [void]$Stream.Seek($offset,[IO.SeekOrigin]::Begin)
        $sample=New-Object byte[] $sampleLength;$position=0
        while ($position -lt $sample.Length) {
            $count=$Stream.Read($sample,$position,$sample.Length-$position)
            if($count -eq 0){break}
            $position+=$count
        }
        $samples+=,[Convert]::ToBase64String($sample,0,$position)
    }
    return ,$samples
}
try {
    $initial=Invoke-RestMethod -Uri ($base+'?brief=true') -TimeoutSec 30 -MaximumRedirection 0
    if($initial.state -in @('cancelled','error')){return}
    Add-Type -AssemblyName System.Windows.Forms
    $dialog=New-Object Windows.Forms.OpenFileDialog
    $dialog.Title='SR - Select images (server JPG preview)'
    $dialog.Filter='Images|*.tif;*.tiff;*.jpg;*.jpeg;*.png'
    $dialog.Multiselect=$true
    try {
        if ($dialog.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) { Send-Json $Callback @{cancelled=$true} | Out-Null; return }
        $paths=@($dialog.FileNames)
    } finally { $dialog.Dispose() }
    if ($paths.Count -gt 16) { throw 'Select at most 16 images at a time.' }
    $files=@()
    foreach ($path in $paths) {
        if(-not (Wait-Ready)){return}
        $info=New-Object IO.FileInfo($path)
        $stream=[IO.File]::OpenRead($path)
        try {
            $samples=Get-SrImageSamples $stream
        } finally { $stream.Dispose() }
        $serverPath=$path
        if($path.StartsWith('\\?\UNC\',[StringComparison]::OrdinalIgnoreCase)){$serverPath='\\'+$path.Substring(8)}
        elseif($path.StartsWith('\\?\',[StringComparison]::OrdinalIgnoreCase)){$serverPath=$path.Substring(4)}
        $files+=@{path=$serverPath;name=$info.Name;size=$info.Length;fingerprint=@{name=$info.Name;size=$info.Length;samples=$samples}}
    }
    $state=Send-Json $Callback @{files=$files}
    for ($i=0;$i -lt $files.Count;$i++) {
        $entry=$state.files[$i]
        if ($entry.state -in @('ready','error')) { continue }
        if ([string]$entry.id -cnotmatch '^[a-f0-9]{64}$') { throw 'Invalid upload identifier.' }
        $stream=[IO.File]::OpenRead([string]$paths[$i])
        try {
            $buffer=New-Object byte[] (4*1024*1024);$offset=[long]0
            while (($read=$stream.Read($buffer,0,$buffer.Length)) -gt 0) {
                do {
                    $control=Invoke-RestMethod -Uri ($base+'?brief=true') -TimeoutSec 30 -MaximumRedirection 0
                    if ($control.state -in @('cancelled','error')) { return }
                    if ($control.state -eq 'paused') { Start-Sleep -Milliseconds 500 }
                } while ($control.state -eq 'paused')
                $chunk=$buffer
                if($read -ne $buffer.Length){$chunk=New-Object byte[] $read;[Buffer]::BlockCopy($buffer,0,$chunk,0,$read)}
                $upload=$base+'/files/'+$entry.id+'/chunks?offset='+$offset+'&brief=true'
                $sent=$false
                while(-not $sent) {
                    try {
                        $state=Invoke-RestMethod -Uri $upload -Method Put -Body $chunk -ContentType 'application/octet-stream' -TimeoutSec 120 -MaximumRedirection 0
                        $sent=$true
                    } catch {
                        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 409) {
                            $latest=Invoke-RestMethod -Uri ($base+'?brief=true') -TimeoutSec 30 -MaximumRedirection 0
                            if($latest.state -in @('cancelled','error')){return}
                            while($latest.state -eq 'paused'){Start-Sleep -Milliseconds 500;$latest=Invoke-RestMethod -Uri ($base+'?brief=true') -TimeoutSec 30 -MaximumRedirection 0}
                            if($latest.state -in @('cancelled','error')){return}
                            if($latest.files[$i].received -eq $offset+$read){$sent=$true}
                            elseif($latest.files[$i].received -ne $offset){throw}
                        }else{throw}
                    }
                }
                $offset+=$read
            }
            if($offset -ne [long]$files[$i].size){throw 'The source file changed or ended before upload completed.'}
        } finally { $stream.Dispose() }
    }
} catch {
    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 410) { return }
    try { Send-Json ($base+'/state') @{state='failed';error=$_.Exception.Message} | Out-Null } catch { }
    throw
}
