# Pure handler validation: never installs the protocol or launches Explorer.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$installer = Join-Path $repoRoot 'frontend\public\client-explorer\Install-SrExplorer.ps1'
$tokens = $null; $parseErrors = $null
[Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors) { throw ($parseErrors | Out-String) }
$source = [IO.File]::ReadAllText($installer)
$match = [regex]::Match($source, "(?s)\`$handler = @'\r?\n(.*?)\r?\n'@")
if (-not $match.Success) { throw 'Handler payload not found.' }
$testDir = Join-Path $PSScriptRoot ('tmp-explorer-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDir | Out-Null
$handlerFile = Join-Path $testDir 'handler.ps1'
$mappingFile = Join-Path $testDir 'mapping.json'
try {
    $match.Groups[1].Value | Set-Content -LiteralPath $handlerFile -Encoding UTF8
    @{ server_root='/DiskArray'; windows_root='W:\' } | ConvertTo-Json | Set-Content -LiteralPath $mappingFile -Encoding UTF8
    function Make-Uri([string]$Path) {
        $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Path)).TrimEnd('=').Replace('+','-').Replace('/','_')
        return 'sr-explorer://open/' + $encoded
    }
    $count = 0
    foreach ($case in @(
        @('/DiskArray', 'W:\'),
        @('/DiskArray/scene with space/a&b$(test)', 'W:\scene with space\a&b$(test)'),
        @(('/DiskArray/' + [char]0x4e2d + [char]0x6587), ('W:\' + [char]0x4e2d + [char]0x6587))
    )) {
        $actual = & $handlerFile -Uri (Make-Uri $case[0]) -ValidateOnly
        if ($actual -cne $case[1]) { throw "Unexpected mapping: $actual" }
        $count++
    }
    foreach ($path in @('/DiskArrayElse/a', '/etc', '/diskarray/a', '/DiskArray/../etc', '/DiskArray/./a', '/DiskArray/a"x',
        '/DiskArray/a|b', '/DiskArray/CON', '/DiskArray/NUL.txt', '/DiskArray/x ', '/DiskArray/a:b', '/DiskArray/a\b', "/DiskArray/a\`nb")) {
        $rejected = $false
        try { & $handlerFile -Uri (Make-Uri $path) -ValidateOnly | Out-Null } catch { $rejected = $true }
        if (-not $rejected) { throw "Accepted invalid path: $path" }
        $count++
    }
    foreach ($uri in @('file:///W:/a', 'sr-explorer://open/QQ?x=y', 'sr-explorer://delete/QQ', 'sr-explorer://open/_w', 'sr-explorer://open/A')) {
        $rejected = $false
        try { & $handlerFile -Uri $uri -ValidateOnly | Out-Null } catch { $rejected = $true }
        if (-not $rejected) { throw "Accepted invalid URI: $uri" }
        $count++
    }
    Write-Output "PASS: $count handler cases; no registry changes or Explorer launch."
} finally {
    Remove-Item -LiteralPath $handlerFile, $mappingFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $testDir
}
