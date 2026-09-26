param(
    [ValidateSet('M0')]
    [string]$Stage = 'M0',
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Docker = 'E:\Docker\DockerDesktop\resources\bin\docker.exe',
    [string]$Report = (Join-Path (Get-Location) 'var\reports\schema-check.json')
)

$ErrorActionPreference = 'Stop'
$reportDir = Split-Path -Parent $Report
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$checks = @()
$status = 'PASS'

try {
    if (-not (Test-Path -LiteralPath $Python)) { throw "Python runtime not found: $Python" }
    if (-not (Test-Path -LiteralPath $Docker)) { throw "Docker executable not found: $Docker" }

    & $Docker compose -f 'deploy\compose.yml' up -d db 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed with exit code $LASTEXITCODE" }

    $env:RAG_DATABASE_URL = 'postgresql+psycopg://rag:rag@127.0.0.1:55432/rag'
    & $Python -m alembic -c 'alembic.ini' upgrade head 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Alembic upgrade failed with exit code $LASTEXITCODE" }

    $inspect = & $Python -c "import json; from sqlalchemy import create_engine, inspect; e=create_engine('postgresql+psycopg://rag:rag@127.0.0.1:55432/rag'); print(json.dumps(sorted(inspect(e).get_table_names())))" 2>&1
    $inspectExit = $LASTEXITCODE
    $checks += [pscustomobject]@{ name='m0_tables'; exit_code=$inspectExit; output=($inspect -join "`n") }
    if ($inspectExit -ne 0) { throw "Schema inspection failed" }
    $tables = ($inspect -join "`n") | ConvertFrom-Json
    $expected = @('alembic_version','document_versions','documents','ingestion_jobs','knowledge_bases')
    $missing = @($expected | Where-Object { $_ -notin $tables })
    if ($missing.Count -ne 0) {
        throw "Missing required M0 tables: $($missing -join ', ')"
    }
} catch {
    $status = 'FAIL'
    $checks += [pscustomobject]@{ name='schema_check'; exit_code=1; output=$_.Exception.Message }
}

$payload = [pscustomobject]@{ stage=$Stage; status=$status; checks=$checks }
$payload | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $Report
Write-Output ($payload | ConvertTo-Json -Depth 8)
if ($status -ne 'PASS') { exit 1 }
