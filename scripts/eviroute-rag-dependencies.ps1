[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'status', 'stop')]
    [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repoRoot 'docker-compose.eviroute-rag.yml'
$projectName = 'eviroute-rag'
$requiredVolumes = @(
    'weknora-python-resume-pg-20261013',
    'weknora-python-resume-redis-20261013'
)

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI was not found on PATH.'
}
if (-not (Test-Path -LiteralPath $composeFile -PathType Leaf)) {
    throw "Compose file is missing: $composeFile"
}

if ($Action -eq 'start') {
    foreach ($volumeName in $requiredVolumes) {
        & docker volume inspect $volumeName --format '{{.Name}}' *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Required existing Docker volume '$volumeName' is missing; refusing to create an empty replacement."
        }
    }

    # Maintenance only. Existing containers must be present; never recreate them.
    & docker compose --project-name $projectName --file $composeFile start postgres redis
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose start failed with exit code $LASTEXITCODE."
    }
    exit 0
}

if ($Action -eq 'status') {
    & docker compose --project-name $projectName --file $composeFile ps --all postgres redis
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose status failed with exit code $LASTEXITCODE."
    }
    exit 0
}

& docker compose --project-name $projectName --file $composeFile stop postgres redis
if ($LASTEXITCODE -ne 0) {
    throw "docker compose stop failed with exit code $LASTEXITCODE."
}
