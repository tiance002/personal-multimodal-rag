param(
    [Parameter(Mandatory=$true)][string]$InputDir,
    [string]$TargetStorageRoot = (Join-Path (Get-Location) ('var\restore-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))),
    [string]$Python = (Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Docker = 'E:\Docker\DockerDesktop\resources\bin\docker.exe',
    [switch]$RestoreDatabase,
    [switch]$KeepTargetDatabase
)

$ErrorActionPreference = 'Stop'
$resolvedInput = (Resolve-Path -LiteralPath $InputDir).Path
$manifestPath = Join-Path $resolvedInput 'manifest.json'
$dumpPath = Join-Path $resolvedInput 'database.sql'
$sourceStorage = Join-Path $resolvedInput 'storage'
if (-not (Test-Path -LiteralPath $manifestPath)) { throw "Manifest not found: $manifestPath" }
if (-not (Test-Path -LiteralPath $sourceStorage)) { throw "Backup storage not found: $sourceStorage" }
New-Item -ItemType Directory -Force -Path $TargetStorageRoot | Out-Null
Get-ChildItem -LiteralPath $sourceStorage -Force | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $TargetStorageRoot -Recurse -Force }
& $Python scripts\backup_manifest.py verify --storage-root $TargetStorageRoot --manifest $manifestPath --database-dump $dumpPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$databaseStatus = 'NOT RUN'
$databaseChecks = $null
$targetDbName = $null
try {
    if ($RestoreDatabase) {
        if (-not (Test-Path -LiteralPath $Docker)) { throw "Docker executable not found: $Docker" }
        $targetDbName = 'rag_restore_' + (Get-Date -Format 'HHmmssfff')
        & $Docker compose -f 'deploy\compose.yml' exec -T db createdb -U rag $targetDbName *> $null
        if ($LASTEXITCODE -ne 0) { throw "createdb failed: $LASTEXITCODE" }
        Get-Content -Raw -LiteralPath $dumpPath | & $Docker compose -f 'deploy\compose.yml' exec -T db psql -U rag -d $targetDbName *> $null
        if ($LASTEXITCODE -ne 0) { throw "database restore failed: $LASTEXITCODE" }
        $targetUrl = "postgresql+psycopg://rag:rag@127.0.0.1:55432/$targetDbName"
        $checkOutput = & $Python scripts\restore_check.py --database-url $targetUrl 2>&1
        if ($LASTEXITCODE -ne 0) { throw "restore invariant check failed: $($checkOutput -join "`n")" }
        $databaseChecks = ($checkOutput -join "`n") | ConvertFrom-Json
        $databaseStatus = 'PASS'
    }
} finally {
    if ($targetDbName -and -not $KeepTargetDatabase) {
        & $Docker compose -f 'deploy\compose.yml' exec -T db dropdb -U rag $targetDbName *> $null
    }
}
$report = [pscustomobject]@{status='PASS';input_dir=$resolvedInput;target_storage_root=(Resolve-Path -LiteralPath $TargetStorageRoot).Path;database_restore=$databaseStatus;database_checks=$databaseChecks}
$reportPath = Join-Path $TargetStorageRoot 'restore-report.json'
$report | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $reportPath
$report | ConvertTo-Json -Depth 8

