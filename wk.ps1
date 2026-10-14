# Local, isolated mock entry only. Never starts/stops/renames Docker containers.
param([ValidateSet('status','api','ui','test')][string]$Action='status')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$Python='E:\RAG quention\.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $Python)) { throw 'Verified Python runtime missing' }
$env:PYTHONDONTWRITEBYTECODE='1'
# Public synthetic fixture only; test connection checks physical DB identity.
$env:WEKNORA_PORT_TEST_DSN='postgresql://port_fixture:synthetic-isolated-fixture@127.0.0.1:56423/weknora_python_port_20261013'
$env:PYTHONPATH=Join-Path $PSScriptRoot 'var\weknora-port\runtime-deps'
switch ($Action) {
 'status' {
  git rev-parse HEAD
  docker ps -a --format '{{.Names}} | {{.Status}} | {{.Ports}} | {{.Image}}'
  Write-Output 'Current isolated PG=127.0.0.1:56423 Redis=127.0.0.1:56424; API mock=56425 UI native=56426'
  Write-Output 'No real-provider approval. API action uses fixture.invalid MockTransport only.'
 }
 'api' {
  # Foreground: Ctrl+C ends only this helper. Existing services are not replaced.
  & $Python -B scripts/weknora_port_browser_fixture.py --ui-scenarios
  exit $LASTEXITCODE
 }
 'ui' {
  $env:VITE_RAG_IMPLEMENTATION='weknora_python'
  $env:VITE_WEKNORA_API_BASE='http://127.0.0.1:56425'
  Push-Location frontend
  try { npm run dev -- --host 127.0.0.1 --port 56426 --strictPort; exit $LASTEXITCODE }
  finally { Pop-Location }
 }
 'test' {
  # Bounded actual-entry mock fixture; do not repeat full frozen ingestion.
  & $Python -B -m pytest tests/weknora_port/test_memory_management_api_pg.py -q -p no:cacheprovider
  exit $LASTEXITCODE
 }
}
