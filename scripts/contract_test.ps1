param(
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Report = (Join-Path (Get-Location) 'var\reports\contract-test.json')
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Python)) { throw "Python runtime not found: $Python" }
& $Python 'scripts\contract_test.py' --report $Report
exit $LASTEXITCODE

