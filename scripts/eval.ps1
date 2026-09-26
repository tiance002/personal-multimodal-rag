param(
    [ValidateSet('Schema','Retrieval')]
    [string]$Mode = 'Schema',
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Report = (Join-Path (Get-Location) 'var\reports\eval-schema.json')
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "Python runtime not found: $Python"
    exit 1
}

if ($Mode -eq 'Schema') {
    & $Python 'scripts\validate_eval.py' --path 'evaluations\core.jsonl' --report $Report
} else {
    & $Python 'scripts\evaluate_retrieval.py' --dataset 'evaluations\core.jsonl' --report $Report
}
exit $LASTEXITCODE
