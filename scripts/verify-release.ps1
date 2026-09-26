param(
    [switch]$Fresh,
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Report = (Join-Path (Get-Location) 'var\reports\verify-release.json'),
    [string]$WithServer = $env:WEBAPP_TESTING_WITH_SERVER,
    [string]$HelperPython = $env:RAG_HELPER_PYTHON
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Report) | Out-Null
$env:RAG_DATABASE_URL = 'postgresql+psycopg://rag:rag@127.0.0.1:55432/rag'

# Machine-independent defaults: no absolute path that only exists on one workstation.
if (-not $WithServer) { $WithServer = Join-Path $env:USERPROFILE '.agents\skills\webapp-testing\scripts\with_server.py' }
if (-not $HelperPython) { $HelperPython = $Python }
if (-not $env:PLAYWRIGHT_CHROME_PATH -and $env:ProgramFiles) {
    $candidateChrome = Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'
    if (Test-Path -LiteralPath $candidateChrome) { $env:PLAYWRIGHT_CHROME_PATH = $candidateChrome }
}
$CmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
$ApiServer = "`"$Python`" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000"
$FrontendServer = 'npm --prefix frontend run dev -- --host 127.0.0.1'
$FrontendPreview = 'npm --prefix frontend run preview -- --host 127.0.0.1 --port 4173'

$checks = @()
function Invoke-Check { param([string]$Name,[scriptblock]$Command); try { $output = & $Command 2>&1; $code = $LASTEXITCODE; if ($null -eq $code) { $code = 0 }; [pscustomobject]@{name=$Name;exit_code=$code;output=($output -join "`n")} } catch { [pscustomobject]@{name=$Name;exit_code=1;output=$_.Exception.Message} } }
$checks += Invoke-Check 'release_preflight' { & $Python scripts\release_preflight.py }
$checks += Invoke-Check 'verify_m0' { & .\scripts\verify-m0.ps1 -Python $Python }
$checks += Invoke-Check 'verify_m1' { & .\scripts\verify-m1.ps1 -Python $Python }
$checks += Invoke-Check 'budget_smoke' { & $Python scripts\smoke_budget.py --database-url 'postgresql+psycopg://rag:rag@127.0.0.1:55432/rag' --report 'var\reports\smoke-budget.json' }
$checks += Invoke-Check 'verify_m2' { & .\scripts\verify-m2.ps1 -Python $Python }
$checks += Invoke-Check 'verify_m3' { & .\scripts\verify-m3.ps1 -Python $Python }
$checks += Invoke-Check 'verify_m4' { & .\scripts\verify-m4.ps1 -Python $Python }
$checks += Invoke-Check 'quality_eval' { & $Python scripts\evaluate_rag_quality.py --report 'var\reports\eval-rag-quality.json' }
$checks += Invoke-Check 'quality_postgres_ollama' { & $Python scripts\smoke_quality_postgres.py --database-url 'postgresql+psycopg://rag:rag@127.0.0.1:55432/rag' --report 'var\reports\smoke-quality-postgres.json' }
$checks += Invoke-Check 'langchain_ollama_smoke' { & $Python scripts\smoke_langchain_ollama.py --report 'var\reports\smoke-langchain-ollama.json' }
$checks += Invoke-Check 'contract_test' { & .\scripts\contract_test.ps1 -Python $Python }
$checks += Invoke-Check 'frontend_build' { & npm --prefix frontend run build }
$checks += Invoke-Check 'compose_config' { & docker compose -f deploy\compose.yml config }
$composeProject = 'rag-v1-verify-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$previousPorts = @{
    POSTGRES_PORT = $env:POSTGRES_PORT
    API_PORT = $env:API_PORT
    FRONTEND_PORT = $env:FRONTEND_PORT
}
try {
    # Isolate release-check containers and data from the user's ordinary Compose stack.
    $env:POSTGRES_PORT = '55433'
    $env:API_PORT = '18001'
    $env:FRONTEND_PORT = '14174'
    $checks += Invoke-Check 'compose_stack_build' { & docker compose -p $composeProject -f deploy\compose.yml build api worker frontend }
    if ($checks[-1].exit_code -eq 0) {
        $checks += Invoke-Check 'compose_runtime_browser' {
            try {
                & docker compose -p $composeProject -f deploy\compose.yml up -d --no-build api worker frontend
                if ($LASTEXITCODE -ne 0) { throw 'Compose stack failed to start' }
                $ready = $false
                for ($attempt = 0; $attempt -lt 45; $attempt++) {
                    try {
                        $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:14174/healthz' -TimeoutSec 3
                        if ($response.StatusCode -eq 200) { $ready = $true; break }
                    } catch { Start-Sleep -Seconds 2 }
                }
                if (-not $ready) { throw 'Compose Nginx/API health did not become ready' }
                $requiredServices = @('db','api','worker','frontend')
                $running = @()
                for ($attempt = 0; $attempt -lt 30; $attempt++) {
                    $running = @(& docker compose -p $composeProject -f deploy\compose.yml ps --status running --services)
                    if (($requiredServices | Where-Object { $_ -notin $running }).Count -eq 0) { break }
                    Start-Sleep -Seconds 1
                }
                foreach ($service in $requiredServices) {
                    if ($service -notin $running) { throw "Compose service '$service' is not running" }
                }
                & $Python scripts\preview_proxy_smoke.py --base-url 'http://127.0.0.1:14174' --expect-server nginx --report 'var\reports\compose-proxy-smoke.json'
                if ($LASTEXITCODE -ne 0) { throw 'Compose browser proxy smoke failed' }
                & $Python scripts\playwright_smoke.py --base-url 'http://127.0.0.1:14174' --report 'var\reports\compose-ui-smoke.json'
                if ($LASTEXITCODE -ne 0) { throw 'Compose browser UI smoke failed' }
            } finally {
                & docker compose -p $composeProject -f deploy\compose.yml down --volumes
            }
        }
    } else {
        $checks += [pscustomobject]@{name='compose_runtime_browser';exit_code=1;output='NOT RUN: current Compose stack did not build'}
    }
} finally {
    $env:POSTGRES_PORT = $previousPorts.POSTGRES_PORT
    $env:API_PORT = $previousPorts.API_PORT
    $env:FRONTEND_PORT = $previousPorts.FRONTEND_PORT
}
if (Test-Path -LiteralPath $WithServer) {
    $checks += Invoke-Check 'frontend_playwright' { & $HelperPython $WithServer --server $ApiServer --port 8000 --server $FrontendServer --port 5173 --timeout 90 -- $CmdExe '/c' 'npm --prefix frontend test' }
    $checks += Invoke-Check 'frontend_ui_smoke' { & $HelperPython $WithServer --server $ApiServer --port 8000 --server $FrontendServer --port 5173 --timeout 90 -- $Python scripts\playwright_smoke.py }
    $checks += Invoke-Check 'frontend_preview_proxy' { & $HelperPython $WithServer --server $ApiServer --port 8000 --server $FrontendPreview --port 4173 --timeout 90 -- $Python scripts\preview_proxy_smoke.py --report 'var\reports\preview-proxy-smoke.json' }
} else {
    $missing = "with_server.py not found at '$WithServer'; set WEBAPP_TESTING_WITH_SERVER to the helper script path."
    $checks += [pscustomobject]@{name='frontend_playwright';exit_code=1;output=$missing}
    $checks += [pscustomobject]@{name='frontend_ui_smoke';exit_code=1;output=$missing}
    $checks += [pscustomobject]@{name='frontend_preview_proxy';exit_code=1;output=$missing}
}
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
