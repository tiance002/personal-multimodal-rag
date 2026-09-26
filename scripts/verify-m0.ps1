param(
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Report = (Join-Path (Get-Location) 'var\reports\verify-m0.json')
)

$ErrorActionPreference = 'Stop'
$reportDir = Split-Path -Parent $Report
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python runtime not found: $Python"
}

$started = Get-Date
$entries = @()

function Invoke-Check {
    param(
        [string]$Name,
        [scriptblock]$Command
    )
    try {
        $output = & $Command 2>&1
        $exitCode = $LASTEXITCODE
        if ($null -eq $exitCode) { $exitCode = 0 }
        return [pscustomobject]@{ name=$Name; exit_code=$exitCode; output=($output -join "`n") }
    } catch {
        return [pscustomobject]@{ name=$Name; exit_code=1; output=$_.Exception.Message }
    }
}

$entries += Invoke-Check -Name 'python_imports' -Command {
    & $Python -c "import fastapi,pydantic,sqlalchemy,alembic,psycopg,pgvector,fitz,PIL,pytest,uvicorn,multipart; print('imports=ok')"
}
$entries += Invoke-Check -Name 'pytest' -Command {
    & $Python -m pytest --basetemp 'var\pytest-tmp-m0' -q
}
$entries += Invoke-Check -Name 'schema_migration' -Command {
    & (Join-Path (Get-Location) 'scripts\schema_check.ps1') -Stage M0
}
$entries += Invoke-Check -Name 'model_probe' -Command {
    & $Python 'scripts\model_probe.py' --report 'var\reports\m0-model-probe.json'
}
$entries += Invoke-Check -Name 'evaluation_schema' -Command {
    & (Join-Path (Get-Location) 'scripts\eval.ps1') -Mode Schema -Python $Python -Report 'var\reports\eval-schema.json'
}

$status = if (($entries | Where-Object { $_.exit_code -ne 0 }).Count -eq 0) { 'PASS' } else { 'FAIL' }
$payload = [pscustomobject]@{
    gate = 'M0'
    status = $status
    started_at = $started.ToUniversalTime().ToString('o')
    finished_at = (Get-Date).ToUniversalTime().ToString('o')
    checks = $entries
}
$payload | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $Report
Write-Output ($payload | ConvertTo-Json -Depth 8)
if ($status -ne 'PASS') { exit 1 }
