# Pure JSON merge tests: no policy / registry / native process changes.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\frontend\public\client-explorer\Chrome-SrExplorerPolicy.ps1') -FunctionsOnly
$origin = 'http://10.10.81.135:80'
$r = Update-SrExplorerChromePolicyJson -RawJson ''
$parsed = $r.Json | ConvertFrom-Json
$rows = @($parsed)
if (-not $r.Changed -or -not $r.Added -or $rows.Count -ne 1 -or
    $rows[0].protocol -cne 'sr-explorer' -or $rows[0].allowed_origins.Count -ne 1 -or
    $rows[0].allowed_origins[0] -cne $origin) { throw 'Default exact origin rule mismatch.' }
$again = Update-SrExplorerChromePolicyJson -RawJson $r.Json
if ($again.Changed -or $again.Added) { throw 'Repeated installation is not idempotent.' }
$empty = Update-SrExplorerChromePolicyJson -RawJson $r.Json -Remove
if (-not $empty.Changed -or $empty.RuleCount -ne 0 -or $empty.Json -cne '[]') { throw 'Owned rule was not removed.' }
$original = '[{"protocol":"zoommtg","allowed_origins":["https://meeting.example.com"]},{"protocol":"sr-explorer","allowed_origins":["https://other.example.com"]}]'
$merged = Update-SrExplorerChromePolicyJson -RawJson $original
$parsed = $merged.Json | ConvertFrom-Json
$rows = @($parsed)
if ($rows.Count -ne 2 -or $rows[0].allowed_origins[0] -cne 'https://meeting.example.com' -or
    $rows[1].allowed_origins.Count -ne 2 -or $rows[1].allowed_origins[0] -cne 'https://other.example.com') { throw 'Existing rules were changed.' }
$restored = Update-SrExplorerChromePolicyJson -RawJson $merged.Json -Remove
if ($restored.Json -cne $original) { throw 'Removal did not preserve unrelated rules.' }
$absent = Update-SrExplorerChromePolicyJson -RawJson $original -Remove
if ($absent.Changed) { throw 'Removal of absent origin should be a no-op.' }
foreach ($raw in @('{}', 'not-json', '[{"protocol":"x","allowed_origins":"*"}]', '[{"protocol":"x","allowed_origins":[2]}]')) {
    $blocked = $false
    try { Update-SrExplorerChromePolicyJson -RawJson $raw | Out-Null } catch { $blocked = $true }
    if (-not $blocked) { throw 'Invalid existing rule was not rejected.' }
}
Write-Output 'PASS: 10 Chrome policy cases (exact origin, merge, repeat, removal, malformed rules); no registry changes.'
