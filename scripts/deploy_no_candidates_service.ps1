param([switch]$Deploy)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskReports = Join-Path $taskRoot 'var/reports/no-candidates-20261011'
$taskEnvFile = Join-Path $taskRoot '.env.local'
$taskImage = 'personal-rag:no-candidates-20261011'
if (!(Test-Path -LiteralPath $taskEnvFile)) { throw 'OWNER_ENV_FILE_MISSING' }
$taskBackup = Get-Content -LiteralPath (Join-Path $taskReports 'business-db-backup.json') -Raw | ConvertFrom-Json
$taskRestore = Get-Content -LiteralPath (Join-Path $taskReports 'migration-quota-isolated.json') -Raw | ConvertFrom-Json
if ($taskRestore.status -ne 'CHECKS_PASSED' -or $taskRestore.backup_sha256 -ne $taskBackup.backup_sha256) {
    throw 'BACKUP_RESTORE_PROOF_REQUIRED'
}
$taskActualHash = (Get-FileHash -LiteralPath (Join-Path $taskReports 'business-db-before-0023.dump') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($taskActualHash -ne $taskBackup.backup_sha256) { throw 'BACKUP_HASH_MISMATCH' }
docker image inspect $taskImage --format '{{.Id}}' *> (Join-Path $taskReports 'deployment-image-id.txt')
if ($LASTEXITCODE -ne 0) { throw 'BUILT_IMAGE_REQUIRED' }

# Public settings only; Docker Compose privately loads the owner's env file.
$env:RAG_SERVICE_MODEL_POLICY_JSON = (Get-Content -LiteralPath 'deploy/service-policy.example.json' -Raw | ConvertFrom-Json | ConvertTo-Json -Depth 8 -Compress)
$env:RAG_CLOUD_ENABLED = 'true'
$env:RAG_EMBEDDING_BACKEND = 'siliconflow'
$env:RAG_EMBEDDING_EGRESS_ENABLED = 'true'
$env:RAG_CHAT_EGRESS_ENABLED = 'true'
$env:RAG_VISION_EGRESS_ENABLED = 'true'
$env:RAG_RERANK_EGRESS_ENABLED = 'false'
$env:RAG_RULE_ROUTER_ENABLED = 'true'
$env:RAG_PREFER_CLOUD = 'true'
$env:RAG_REDIS_URL = 'redis://redis:6379/0'
$env:RAG_EMBEDDING_TOKENIZER_PATH = '/opt/model-assets/bge-m3/tokenizer.json'
$env:RAG_XING_CAPACITY_RESOURCES = '/opt/model-assets/xing4.0/baae3c3e813cad5f888f1f485cfff659c89076c5'
$env:RAG_DEEPSEEK_TOKENIZER_PATH = '/opt/model-assets/deepseek-v41/tokenizer.json'
# The shared one-million Token authorization cannot exceed USD 1.20 at the
# verified peak quote. Existing cost/UNKNOWN records are retained by the gate.
$env:RAG_MONTHLY_CLOUD_BUDGET_MICROUNITS = '1200000'
$env:RAG_LANGFUSE_ENABLED = 'false'
$env:RAG_LANGFUSE_CAPTURE_CONTENT = 'false'
$env:POSTGRES_PORT = '25438'
$env:API_PORT = '8000'
$taskOverride = Join-Path $taskReports 'ordinary-service.override.yml'
@"
services:
  api:
    image: $taskImage
  worker:
    image: $taskImage
    command: ["python", "-m", "backend.app.workers.ingestion", "--poll-seconds", "2"]
"@ | Set-Content -LiteralPath $taskOverride -Encoding utf8
$taskCompose = @('compose', '--env-file', $taskEnvFile, '-p', 'raglocalfirst0930', '-f', 'deploy/compose.yml', '-f', $taskOverride)
& docker @taskCompose config --quiet *> (Join-Path $taskReports 'compose-private-validation.txt')
if ($LASTEXITCODE -ne 0) { throw 'COMPOSE_CONFIG_INVALID_PRIVATE_LOG_NOT_DISPLAYED' }
if (!$Deploy) { Write-Output 'PREPARED_ONLY; business migration NOT RUN'; exit 0 }

# Preserve the actual DB/storage identity: no DB or frontend recreation.
$taskDbId = & docker inspect raglocalfirst0930-db-1 --format '{{.Id}}'
if ($LASTEXITCODE -ne 0 -or !$taskDbId.StartsWith('5c9a6eacb994')) { throw 'ACTUAL_DB_IDENTITY_CHANGED' }
$taskCurrentRevision = & docker exec raglocalfirst0930-db-1 psql -U rag -d rag -XAt -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version;'
if ($LASTEXITCODE -ne 0 -or $taskCurrentRevision.Trim() -notin @('0013_message_run_link', '0023_service_token_policy', '0024_input_route_attempts')) { throw 'BUSINESS_REVISION_CHANGED_RECHECK_REQUIRED' }
if (Test-Path -LiteralPath 'alembic/versions/0024_input_route_attempts.py') {
    $taskRouteProof = Get-Content -LiteralPath (Join-Path $taskReports 'migration-input-route-isolated-20261012.json') -Raw | ConvertFrom-Json
    if ($taskRouteProof.status -ne 'CHECKS_PASSED' -or $taskRouteProof.input_route_check.provider_calls -ne 0) { throw 'INPUT_ROUTE_MIGRATION_PROOF_REQUIRED' }
    $taskRouteBackup = Get-Content -LiteralPath (Join-Path $taskReports 'business-db-before-0024.json') -Raw | ConvertFrom-Json
    $taskRouteHash = (Get-FileHash -LiteralPath (Join-Path $taskReports 'business-db-before-0024.dump') -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($taskRouteHash -ne $taskRouteBackup.sha256) { throw 'INPUT_ROUTE_BACKUP_HASH_MISMATCH' }
}
& docker @taskCompose up -d --no-build --no-deps redis *> (Join-Path $taskReports 'redis-start.txt')
if ($LASTEXITCODE -ne 0) { throw 'REDIS_START_FAILED' }
$taskRedisReady = $false
for ($taskAttempt = 0; $taskAttempt -lt 20; $taskAttempt++) {
    $taskPing = & docker exec raglocalfirst0930-redis-1 redis-cli ping 2>$null
    if ($LASTEXITCODE -eq 0 -and $taskPing -eq 'PONG') { $taskRedisReady = $true; break }
    Start-Sleep -Milliseconds 500
}
if (!$taskRedisReady) { throw 'REDIS_NOT_READY' }
& docker @taskCompose up -d --no-build --no-deps api worker *> (Join-Path $taskReports 'ordinary-service-start.txt')
if ($LASTEXITCODE -ne 0) { throw 'SERVICE_START_FAILED' }
Write-Output 'SERVICES_STARTED; configuration and real endpoint checks still required'
