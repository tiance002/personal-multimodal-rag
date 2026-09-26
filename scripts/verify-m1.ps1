param(
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$DatabaseUrl = 'postgresql+psycopg://rag:rag@127.0.0.1:55432/rag',
    [string]$Report = (Join-Path (Get-Location) 'var\reports\verify-m1.json')
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Report) | Out-Null
$env:RAG_DATABASE_URL = $DatabaseUrl
$checks = @()
function Invoke-Check { param([string]$Name,[scriptblock]$Command); try { $output = & $Command 2>&1; $code = $LASTEXITCODE; if ($null -eq $code) { $code = 0 }; [pscustomobject]@{name=$Name;exit_code=$code;output=($output -join "`n")} } catch { [pscustomobject]@{name=$Name;exit_code=1;output=$_.Exception.Message} } }
$checks += Invoke-Check 'migration_head' { & $Python -m alembic -c alembic.ini upgrade head }
$checks += Invoke-Check 'm1_tests' { & $Python -m pytest --basetemp 'var\pytest-tmp-m1' backend/tests/test_chunking.py backend/tests/test_fusion.py backend/tests/test_hybrid_retrieval.py backend/tests/test_quality_gate.py backend/tests/test_model_policy.py backend/tests/test_rag_orchestrator.py backend/tests/test_budget_gate.py backend/tests/test_budget_orchestrator.py backend/tests/test_citation_resolution.py backend/tests/test_evidence.py -q }
$checks += Invoke-Check 'm1_real_smoke' { & $Python scripts\smoke_m1.py --database-url $DatabaseUrl --real-model --report 'var\reports\smoke-m1.json' }
$checks += Invoke-Check 'api_sse_smoke' { & $Python scripts\smoke_api.py }
$status = if (($checks | Where-Object {$_.exit_code -ne 0}).Count -eq 0) {'PASS'} else {'FAIL'}
$payload = [pscustomobject]@{gate='M1';status=$status;checks=$checks}
$payload | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $Report
$payload | ConvertTo-Json -Depth 8
if ($status -ne 'PASS') { exit 1 }
