param(
    [switch]$Fresh,
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Report = (Join-Path (Get-Location) 'var\reports\verify-release.json')
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Report) | Out-Null
$env:RAG_DATABASE_URL = 'postgresql+psycopg://rag:rag@127.0.0.1:55432/rag'
$checks = @()
function Invoke-Check { param([string]$Name,[scriptblock]$Command); try { $output = & $Command 2>&1; $code = $LASTEXITCODE; if ($null -eq $code) { $code = 0 }; [pscustomobject]@{name=$Name;exit_code=$code;output=($output -join "`n")} } catch { [pscustomobject]@{name=$Name;exit_code=1;output=$_.Exception.Message} } }
$checks += Invoke-Check 'verify_m0' { & .\scripts\verify-m0.ps1 -Python $Python }
$checks += Invoke-Check 'verify_m1' { & .\scripts\verify-m1.ps1 -Python $Python }
$checks += Invoke-Check 'budget_smoke' { & $Python scripts\smoke_budget.py --database-url 'postgresql+psycopg://rag:rag@127.0.0.1:55432/rag' --report 'var\reports\smoke-budget.json' }
$checks += Invoke-Check 'verify_m2' { & .\scripts\verify-m2.ps1 -Python $Python }
$checks += Invoke-Check 'verify_m3' { & .\scripts\verify-m3.ps1 -Python $Python }
$checks += Invoke-Check 'verify_m4' { & .\scripts\verify-m4.ps1 -Python $Python }
$checks += Invoke-Check 'contract_test' { & .\scripts\contract_test.ps1 -Python $Python }
$checks += Invoke-Check 'frontend_build' { & npm --prefix frontend run build }
$checks += Invoke-Check 'frontend_playwright' { & 'D:\Drivers\python\python.exe' 'C:\Users\22088\.agents\skills\webapp-testing\scripts\with_server.py' --server '"E:\RAG quention\.venv\Scripts\python.exe" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000' --port 8000 --server 'npm --prefix frontend run dev -- --host 127.0.0.1' --port 5173 --timeout 90 -- 'C:\WINDOWS\System32\cmd.exe' '/c' 'npm --prefix frontend test' }
$checks += Invoke-Check 'frontend_ui_smoke' { & 'D:\Drivers\python\python.exe' 'C:\Users\22088\.agents\skills\webapp-testing\scripts\with_server.py' --server '"E:\RAG quention\.venv\Scripts\python.exe" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000' --port 8000 --server 'npm --prefix frontend run dev -- --host 127.0.0.1' --port 5173 --timeout 90 -- 'E:\RAG quention\.venv\Scripts\python.exe' scripts\playwright_smoke.py }
$checks += Invoke-Check 'backup' { & .\scripts\backup.ps1 }
$backupPath = $null
if (($checks[-1].exit_code -eq 0)) {
    try { $backupPath = ($checks[-1].output | ConvertFrom-Json).backup_dir } catch { $backupPath = $null }
}
if ($backupPath) { $checks += Invoke-Check 'restore' { & .\scripts\restore.ps1 -InputDir $backupPath -RestoreDatabase } } else { $checks += [pscustomobject]@{name='restore';exit_code=1;output='backup path unavailable'} }
$status = if (($checks | Where-Object {$_.exit_code -ne 0}).Count -eq 0) {'PASS'} else {'FAIL'}
$payload = [pscustomobject]@{gate='V1.0_RELEASE';status=$status;checks=$checks;fresh=[bool]$Fresh;owner_acceptance='REQUIRED';git='NOT AVAILABLE'}
$payload | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $Report
$payload | ConvertTo-Json -Depth 8
if ($status -ne 'PASS') { exit 1 }
