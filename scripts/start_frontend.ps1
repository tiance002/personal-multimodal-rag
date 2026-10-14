$ErrorActionPreference = 'Stop'
$SnapshotRoot = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $SnapshotRoot 'frontend')
try { npm.cmd run dev -- --host 127.0.0.1; exit $LASTEXITCODE }
finally { Pop-Location }
