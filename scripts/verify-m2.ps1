param(
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Report = (Join-Path (Get-Location) 'var\reports\verify-m2.json')
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Report) | Out-Null
$checks = @()
function Invoke-Check { param([string]$Name,[scriptblock]$Command); try { $output = & $Command 2>&1; $code = $LASTEXITCODE; if ($null -eq $code) { $code = 0 }; [pscustomobject]@{name=$Name;exit_code=$code;output=($output -join "`n")} } catch { [pscustomobject]@{name=$Name;exit_code=1;output=$_.Exception.Message} } }
$checks += Invoke-Check 'ocr_language_data' { & .\scripts\setup_ocr.ps1 }
$checks += Invoke-Check 'm2_tests' { & $Python -m pytest --basetemp 'var\pytest-tmp-m2' backend/tests/test_multimodal_ingestion.py backend/tests/test_pdf_ocr.py backend/tests/test_postgres_ocr_lineage.py backend/tests/test_egress_matrix.py backend/tests/test_storage.py backend/tests/test_ingestion_state_machine.py -q }
$checks += Invoke-Check 'm2_real_smoke' { & $Python scripts\smoke_m2.py }
$status = if (($checks | Where-Object {$_.exit_code -ne 0}).Count -eq 0) {'PASS'} else {'FAIL'}
$payload = [pscustomobject]@{gate='M2';status=$status;checks=$checks}
$payload | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $Report
$payload | ConvertTo-Json -Depth 8
if ($status -ne 'PASS') { exit 1 }
