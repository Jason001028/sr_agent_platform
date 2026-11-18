# Client-only Chrome policy. FunctionsOnly is for tests; never changes registry.
[CmdletBinding()]
param([switch]$Remove, [switch]$FunctionsOnly)
$ErrorActionPreference = 'Stop'

function Update-SrExplorerChromePolicyJson {
    param([string]$RawJson, [switch]$Remove)
    $origin = 'http://10.10.81.135:80'
    $rows = New-Object 'System.Collections.Generic.List[object]'
    if (-not [string]::IsNullOrWhiteSpace($RawJson)) {
        if (-not $RawJson.Trim().StartsWith('[')) { throw 'Existing Chrome policy is not an array; leaving it unchanged.' }
        $parsed = $RawJson | ConvertFrom-Json
        foreach ($entry in $parsed) {
            if (-not $entry -or $entry.protocol -isnot [string] -or $entry.allowed_origins -isnot [System.Array]) {
                throw 'Existing Chrome policy has an invalid entry; leaving it unchanged.'
            }
            foreach ($value in $entry.allowed_origins) {
                if ($value -isnot [string]) { throw 'Existing Chrome policy has a non-string origin; leaving it unchanged.' }
            }
            $rows.Add($entry)
        }
    }
    $changed = $false; $added = $false
    if ($Remove) {
        for ($i = $rows.Count - 1; $i -ge 0; $i--) {
            $entry = $rows[$i]
            if ($entry.protocol -ceq 'sr-explorer' -and @($entry.allowed_origins) -ccontains $origin) {
                $entry.allowed_origins = @($entry.allowed_origins | Where-Object { $_ -cne $origin })
                if ($entry.allowed_origins.Count -eq 0) { $rows.RemoveAt($i) }
                $changed = $true
            }
        }
    } else {
        $exists = @($rows | Where-Object { $_.protocol -ceq 'sr-explorer' -and @($_.allowed_origins) -ccontains $origin }).Count -gt 0
        if (-not $exists) {
            $entry = $rows | Where-Object { $_.protocol -ceq 'sr-explorer' } | Select-Object -First 1
            if ($entry) { $entry.allowed_origins = @($entry.allowed_origins) + @($origin) }
            else { $rows.Add([pscustomobject]@{ protocol='sr-explorer'; allowed_origins=@($origin) }) }
            $changed = $true; $added = $true
        }
    }
    $json = ConvertTo-Json -InputObject @($rows.ToArray()) -Depth 30 -Compress
    return @{ Changed=$changed; Added=$added; Json=$json; RuleCount=$rows.Count }
}

if ($FunctionsOnly) { return }
$policyKey = 'HKCU:\Software\Policies\Google\Chrome'
$valueName = 'AutoLaunchProtocolsFromOrigins'
$origin = 'http://10.10.81.135:80'
$stateDir = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'SrAgentExplorer'
$stateFile = Join-Path $stateDir 'chrome-policy.json'
$state = $null
if (Test-Path -LiteralPath $stateFile) {
    $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    if ($state.origin -cne $origin) { throw 'Unexpected policy ownership record; refusing to replace it.' }
}
if ($Remove -and (-not $state -or -not $state.added)) {
    if (Test-Path -LiteralPath $stateFile) { Remove-Item -LiteralPath $stateFile }
    return
}
$raw = $null
if (Test-Path -LiteralPath $policyKey) {
    $key = Get-Item -LiteralPath $policyKey
    if ($key.GetValueNames() -contains $valueName) {
        if ($key.GetValueKind($valueName) -ne [Microsoft.Win32.RegistryValueKind]::String) {
            throw 'Existing Chrome policy has an unexpected registry type; leaving it unchanged.'
        }
        $raw = [string]$key.GetValue($valueName)
    }
}
$result = Update-SrExplorerChromePolicyJson -RawJson $raw -Remove:$Remove
if (-not $Remove) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    # Write ownership before changing policy, so an interrupted install is recoverable.
    @{ origin=$origin; added=($result.Added -or ($state -and $state.added)) } |
        ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
}
if ($result.Changed) {
    if ($Remove -and $result.RuleCount -eq 0) {
        Remove-ItemProperty -LiteralPath $policyKey -Name $valueName
    } else {
        New-Item -Path $policyKey -Force | Out-Null
        New-ItemProperty -LiteralPath $policyKey -Name $valueName -Value $result.Json -PropertyType String -Force | Out-Null
    }
}
if ($Remove) {
    Remove-Item -LiteralPath $stateFile -ErrorAction SilentlyContinue
    Write-Host 'Removed only the SR Explorer origin rule added by this installer.'
} else {
    Write-Host "Chrome policy configured: $origin -> sr-explorer"
    Write-Host 'Open chrome://policy, click Reload policies, and check AutoLaunchProtocolsFromOrigins.'
    Write-Host 'If the policy is ignored or overridden, ask your company IT to deploy the same rule.'
    if (Test-Path 'HKLM:\Software\Policies\Google\Chrome') {
        $machineKey = Get-Item 'HKLM:\Software\Policies\Google\Chrome'
        if ($machineKey.GetValueNames() -contains $valueName) {
            Write-Warning 'A machine-level Chrome rule exists and may override this per-user rule. Check chrome://policy.'
        }
    }
}
