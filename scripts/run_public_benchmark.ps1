[CmdletBinding()]
param(
    [ValidateSet('preflight', 'fetch', 'prepare', 'baseline', 'optimize', 'qa', 'validate', 'sync', 'report')]
    [string]$Phase = 'preflight',
    [ValidateSet('scifact', 'miracl-zh', 'longbench-zh')]
    [string]$Dataset = 'scifact',
    [ValidateSet('smoke', 'standard')]
    [string]$Profile = 'smoke',
    [ValidateSet('development', 'locked_holdout')]
    [string]$Split = 'development',
    [string]$DataRoot = 'D:\RAG-Public-Bench',
    [string]$PythonPath = '',
    [int]$MaxCases = 0,
    [int]$ChunkSize = 1200,
    [int]$ChunkOverlap = 120,
    [int]$TopK = 5,
    [int]$CandidateK = 32,
    [int]$RrfK = 60,
    [int]$ContextBudgetChars = 8000,
    [string]$PostgresContainer = 'rag-eval-trust0928-db',
    [switch]$DryRun,
    [switch]$Help
)

if ($Help) {
    @'
Usage: .\scripts\run_public_benchmark.ps1 [-Phase <phase>] [-Dataset <dataset>] [-Profile smoke|standard] [-DryRun]

Phases: preflight, fetch, prepare, baseline, optimize, qa, validate, sync, report
Datasets: scifact, miracl-zh, longbench-zh
Default data root: D:\RAG-Public-Bench

Examples:
  .\scripts\run_public_benchmark.ps1 -Help
  .\scripts\run_public_benchmark.ps1 -Phase preflight -Dataset scifact -DryRun
  .\scripts\run_public_benchmark.ps1 -Phase prepare -Dataset miracl-zh
  .\scripts\run_public_benchmark.ps1 -Phase baseline -Dataset scifact -Profile standard
  .\scripts\run_public_benchmark.ps1 -Phase validate -Dataset scifact -Profile standard

The script reads local PostgreSQL credentials from a running isolated Docker
container into a child-process environment variable. It never writes them to
the repository or to benchmark reports.
'@
    exit 0
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $repoRoot '.venv\Scripts\python.exe'),
        'E:\RAG quention\.venv\Scripts\python.exe'
    )
    $PythonPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $PythonPath) {
        $python = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($python) { $PythonPath = $python.Source }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    Write-Error 'Python runtime not found. Pass -PythonPath.'
    exit 2
}

$requiresDatabase = $Phase -in @('preflight', 'baseline', 'optimize', 'qa', 'validate') -and -not $DryRun
$setDatabaseEnvironment = $false
if ($requiresDatabase -and -not $env:RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL) {
    $docker = Get-Command docker.exe -ErrorAction SilentlyContinue
    if (-not $docker) {
        Write-Error 'Local benchmark PostgreSQL container is unavailable.'
        exit 2
    }
    $dockerJson = & $docker.Source inspect $PostgresContainer 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'Local benchmark PostgreSQL container is unavailable.'
        exit 2
    }
    $container = ($dockerJson | ConvertFrom-Json)[0]
    if (-not $container.State.Running) {
        Write-Error 'Local benchmark PostgreSQL container is not running.'
        exit 2
    }
    $environment = @{}
    foreach ($item in $container.Config.Env) {
        $parts = $item -split '=', 2
        if ($parts.Count -eq 2) { $environment[$parts[0]] = $parts[1] }
    }
    $port = $container.NetworkSettings.Ports.'5432/tcp' | Select-Object -First 1
    if (-not $environment.POSTGRES_USER -or -not $environment.POSTGRES_PASSWORD -or -not $port.HostPort) {
        Write-Error 'Local benchmark PostgreSQL connection details are incomplete.'
        exit 2
    }
    $user = [Uri]::EscapeDataString($environment.POSTGRES_USER)
    $password = [Uri]::EscapeDataString($environment.POSTGRES_PASSWORD)
    $env:RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL = "postgresql+psycopg://${user}:${password}@127.0.0.1:$($port.HostPort)/postgres"
    $setDatabaseEnvironment = $true
}

$arguments = @('-m', 'eval_center.public_benchmark_cli', '--phase', $Phase, '--dataset', $Dataset,
    '--profile', $Profile, '--split', $Split, '--data-root', $DataRoot,
    '--chunk-size', "$ChunkSize", '--chunk-overlap', "$ChunkOverlap", '--top-k', "$TopK",
    '--candidate-k', "$CandidateK", '--rrf-k', "$RrfK", '--context-budget-chars', "$ContextBudgetChars")
if ($MaxCases -gt 0) { $arguments += @('--max-cases', "$MaxCases") }
if ($DryRun) { $arguments += '--dry-run' }

Push-Location $repoRoot
try {
    & $PythonPath @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
    if ($setDatabaseEnvironment) {
        Remove-Item Env:RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL -ErrorAction SilentlyContinue
    }
}
