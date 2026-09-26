param(
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Report = (Join-Path (Get-Location) 'var\reports\verify-m3.json')
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Report) | Out-Null
$checks = @()
function Invoke-Check { param([string]$Name,[scriptblock]$Command); try { $output = & $Command 2>&1; $code = $LASTEXITCODE; if ($null -eq $code) { $code = 0 }; [pscustomobject]@{name=$Name;exit_code=$code;output=($output -join "`n")} } catch { [pscustomobject]@{name=$Name;exit_code=1;output=$_.Exception.Message} } }
$checks += Invoke-Check 'm3_tests' { & $Python -m pytest --basetemp 'var\pytest-tmp-m3' backend/tests/test_graph_evidence.py backend/tests/test_graph_failure_isolation.py backend/tests/test_quality_gate.py -q }
$checks += Invoke-Check 'evaluation_schema' { & $Python scripts\validate_eval.py --path evaluations\core.jsonl --report var\reports\eval-schema.json }
$checks += Invoke-Check 'incremental_evaluation_schema' { & $Python scripts\validate_eval.py --path evaluations\incremental.jsonl --report var\reports\eval-incremental-schema.json }
$checks += Invoke-Check 'evaluation_retrieval' { & $Python scripts\evaluate_retrieval.py --report var\reports\eval-retrieval.json }
$checks += Invoke-Check 'm3_real_smoke' { & $Python scripts\smoke_m3.py }
$status = if (($checks | Where-Object {$_.exit_code -ne 0}).Count -eq 0) {'PASS'} else {'FAIL'}
$payload = [pscustomobject]@{gate='M3';status=$status;checks=$checks}
$payload | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $Report
$payload | ConvertTo-Json -Depth 8
if ($status -ne 'PASS') { exit 1 }
