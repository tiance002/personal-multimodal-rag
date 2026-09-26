param(
    [string]$Report = (Join-Path (Get-Location) 'var\reports\v1-release-report.json'),
    [string]$Markdown = (Join-Path (Get-Location) 'var\reports\v1-release-report.md')
)

$ErrorActionPreference = 'Stop'
$paths = @(
    'var\reports\verify-release.json','var\reports\verify-m0.json','var\reports\verify-m1.json','var\reports\verify-m2.json','var\reports\verify-m3.json','var\reports\verify-m4.json',
    'var\reports\contract-test.json','var\reports\eval-retrieval.json','var\reports\smoke-m2.json','var\reports\smoke-m4.json','var\reports\frontend-smoke.json'
)
$checks = foreach ($path in $paths) {
    if (Test-Path -LiteralPath $path) {
        try { $json = Get-Content -Raw -LiteralPath $path | ConvertFrom-Json; [pscustomobject]@{name=$path;status=([string]$json.status);evidence=$path} }
        catch { [pscustomobject]@{name=$path;status='FAIL';evidence=$path;error=$_.Exception.Message} }
    } else { [pscustomobject]@{name=$path;status='NOT RUN';evidence=$path} }
}
$status = if (($checks | Where-Object {$_.status -in @('FAIL','BLOCKED','NOT RUN','NOT IMPLEMENTED')}).Count -eq 0) {'PASS'} else {'INCOMPLETE'}
$result = [pscustomobject]@{release='personal-rag-v1.0';status=$status;generated_at=(Get-Date).ToUniversalTime().ToString('o');checks=$checks;owner_acceptance='REQUIRED';git_commit='NOT AVAILABLE';git_tag='NOT CREATED'}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Report) | Out-Null
$result | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $Report
$lines = @('# Personal RAG V1.0 release evidence', '', "Status: **$status**", '', '| Check | Status | Evidence |', '|---|---|---|')
foreach ($check in $checks) { $lines += "| $($check.name) | $($check.status) | $($check.evidence) |" }
$lines += '', 'Owner acceptance: REQUIRED; no tag was created.'
$lines -join "`n" | Set-Content -Encoding UTF8 -LiteralPath $Markdown
$result | ConvertTo-Json -Depth 8
if ($status -ne 'PASS') { exit 1 }
