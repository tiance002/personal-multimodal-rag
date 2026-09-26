param(
    [ValidateSet('Schema','Retrieval','Quality')]
    [string]$Mode = 'Schema',
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Report = ''
)

$ErrorActionPreference = 'Stop'
if (-not $Report) {
    $reportName = switch ($Mode) {
        'Schema' { 'eval-schema.json' }
        'Retrieval' { 'eval-retrieval.json' }
        'Quality' { 'eval-rag-quality.json' }
    }
    $Report = Join-Path (Get-Location) "var\reports\$reportName"
}
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Error "Python runtime not found: $Python"
    exit 1
}

if ($Mode -eq 'Schema') {
    & $Python 'scripts\validate_eval.py' --path 'evaluations\core.jsonl' --report $Report
} elseif ($Mode -eq 'Retrieval') {
    & $Python 'scripts\evaluate_retrieval.py' --dataset 'evaluations\core.jsonl' --report $Report
} else {
    & $Python 'scripts\evaluate_rag_quality.py' --dataset 'evaluations\quality_v1.jsonl' --report $Report
}
exit $LASTEXITCODE
