param(
    [string]$OutputDir = (Join-Path (Get-Location) 'var\backups'),
    [string]$StorageRoot = (Join-Path (Get-Location) 'var\storage'),
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Docker = 'E:\Docker\DockerDesktop\resources\bin\docker.exe'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Python)) { throw "Python runtime not found: $Python" }
if (-not (Test-Path -LiteralPath $Docker)) { throw "Docker executable not found: $Docker" }
$backupDir = Join-Path $OutputDir ("backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$backupStorage = Join-Path $backupDir 'storage'
$dumpPath = Join-Path $backupDir 'database.sql'
$manifestPath = Join-Path $backupDir 'manifest.json'
New-Item -ItemType Directory -Force -Path $backupStorage | Out-Null

& $Docker compose -f 'deploy\compose.yml' up -d db *> $null
if ($LASTEXITCODE -ne 0) { throw "Database service could not start: $LASTEXITCODE" }
$dump = & $Docker compose -f 'deploy\compose.yml' exec -T db pg_dump -U rag -d rag 2>&1
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed: $($dump -join "`n")" }
[IO.File]::WriteAllText($dumpPath, ($dump -join "`n") + "`n", [Text.UTF8Encoding]::new($false))

if (Test-Path -LiteralPath $StorageRoot) {
    Get-ChildItem -LiteralPath $StorageRoot -Force | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $backupStorage -Recurse -Force }
}
$env:RAG_DATABASE_URL = 'postgresql+psycopg://rag:rag@127.0.0.1:55432/rag'
$revisionOutput = & $Python -m alembic -c alembic.ini current 2>&1
$revision = ($revisionOutput | Where-Object { $_ -match '^[0-9a-z_]+' } | Select-Object -Last 1)
if (-not $revision) { $revision = 'UNKNOWN' }
$modelReport = Join-Path (Get-Location) 'var\reports\m0-model-probe.json'
& $Python scripts\backup_manifest.py create --storage-root $backupStorage --database-dump $dumpPath --migration-revision $revision --model-report $modelReport --output $manifestPath *> $null
if ($LASTEXITCODE -ne 0) { throw "Backup manifest creation failed: $LASTEXITCODE" }
$result = [pscustomobject]@{status='PASS';backup_dir=(Resolve-Path $backupDir).Path;manifest=(Resolve-Path $manifestPath).Path;database_dump=(Resolve-Path $dumpPath).Path}
$result | ConvertTo-Json -Depth 8
