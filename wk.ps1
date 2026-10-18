# Sole local application entry. Explicit configuration; never loads legacy dotenv.
param(
 [Parameter(Position=0)][ValidateSet('start','stop','status','check')][string]$Action='status',
 [Parameter(Mandatory=$true)][string]$Config
)
$ErrorActionPreference='Stop'
$settings=Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
if (-not $settings.python -or -not (Test-Path -LiteralPath $settings.python -PathType Leaf)) { throw 'Explicit Python executable is missing.' }
& $settings.python -B (Join-Path $PSScriptRoot 'scripts/local_runtime.py') $Action --config $Config
exit $LASTEXITCODE
