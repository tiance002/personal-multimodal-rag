[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$HostName,
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z_][A-Za-z0-9_.-]{0,31}$')][string]$User,
    [ValidateRange(1, 65535)][int]$Port = 22,
    [Parameter(Mandatory = $true)][string]$IdentityFile,
    [string]$PythonExecutable = 'python.exe',
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$identityPath = (Resolve-Path -LiteralPath $IdentityFile -ErrorAction Stop).Path
$knownHostsPath = Join-Path $env:USERPROFILE '.ssh\known_hosts'
if (-not (Test-Path -LiteralPath $knownHostsPath -PathType Leaf)) {
    throw 'Strict SSH host verification requires an existing known_hosts file.'
}
foreach ($toolName in @('ssh.exe', 'sftp.exe', 'tar.exe', 'python.exe')) {
    if (-not (Get-Command $toolName -ErrorAction SilentlyContinue)) {
        throw "Required local command is missing: $toolName"
    }
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Push-Location $repositoryRoot
try {
    $testPythonOutput = & $PythonExecutable -m eval_center.cli --help 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'The local Python runtime cannot load the evaluation-center CLI.' }
    $codeSha = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $codeSha -notmatch '^[0-9a-f]{40}$') { throw 'Cannot identify Git HEAD.' }
    if ((& git status --porcelain)) { throw 'Commit the deployment source first; dirty worktrees cannot be deployed.' }
    if (-not $Apply) {
        Write-Output 'Preview only. No SSH, SFTP, cleanup, or remote deployment was performed. Add -Apply to deploy the loopback-only service.'
        exit 0
    }

    $token = [Guid]::NewGuid().ToString('N')
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $workingDirectory = Join-Path $tempRoot "rag-eval-deploy-$token"
    $stageDirectory = Join-Path $workingDirectory 'payload'
    $archivePath = Join-Path $workingDirectory 'payload.tar.gz'
    New-Item -ItemType Directory -Path (Join-Path $stageDirectory 'eval_center/static') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $stageDirectory 'deploy/eval_center') -Force | Out-Null
    foreach ($module in @('__init__.py', 'contracts.py', 'contracts_v2.py', 'store.py', 'server.py', 'cli.py', 'verification.py', 'gold.py', 'metrics.py', 'quality.py', 'source_metrics.py', 'telemetry.py')) {
        Copy-Item -LiteralPath (Join-Path $repositoryRoot "eval_center/$module") -Destination (Join-Path $stageDirectory "eval_center/$module")
    }
    Copy-Item -LiteralPath (Join-Path $repositoryRoot 'eval_center/static/index.html') -Destination (Join-Path $stageDirectory 'eval_center/static/index.html')
    Copy-Item -LiteralPath (Join-Path $repositoryRoot 'deploy/eval_center/rag-eval.service') -Destination (Join-Path $stageDirectory 'deploy/eval_center/rag-eval.service')
    Copy-Item -LiteralPath (Join-Path $repositoryRoot 'deploy/eval_center/install.sh') -Destination (Join-Path $stageDirectory 'deploy/eval_center/install.sh')
    $installerPath = Join-Path $stageDirectory 'deploy/eval_center/install.sh'
    [IO.File]::WriteAllText($installerPath, [IO.File]::ReadAllText($installerPath).Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $stageDirectory 'CODE_SHA'), "$codeSha`n", [Text.UTF8Encoding]::new($false))

    & tar.exe -czf $archivePath -C $stageDirectory eval_center deploy/eval_center CODE_SHA
    if ($LASTEXITCODE -ne 0) { throw 'Local deployment archive creation failed.' }

    $remoteArchive = "/tmp/rag-eval-deploy-$token.tar.gz"
    $remoteStage = "/tmp/rag-eval-deploy-$token"
    $batchPath = Join-Path $workingDirectory 'sftp.batch'
    $localSftpPath = $archivePath.Replace('\', '/')
    Set-Content -LiteralPath $batchPath -Encoding ascii -Value "put `"$localSftpPath`" $remoteArchive"
    $sshCommon = @(
        '-o', 'BatchMode=yes',
        '-o', 'IdentitiesOnly=yes',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', "UserKnownHostsFile=$knownHostsPath"
    )
    $sftpArguments = @('-P', [string]$Port, '-i', $identityPath, '-b', $batchPath) + $sshCommon + @("$User@$HostName")
    & sftp.exe @sftpArguments
    if ($LASTEXITCODE -ne 0) { throw 'Strict SFTP upload of the deployment package failed.' }

    $remoteCommand = "set -eu; mkdir -m 0700 '$remoteStage'; tar -xzf '$remoteArchive' -C '$remoteStage'; bash '$remoteStage/deploy/eval_center/install.sh'; rm -rf -- '$remoteStage'; rm -f -- '$remoteArchive'"
    $sshArguments = @('-p', [string]$Port, '-i', $identityPath) + $sshCommon + @("$User@$HostName", $remoteCommand)
    & ssh.exe @sshArguments
    if ($LASTEXITCODE -ne 0) { throw 'Remote installer or loopback health check failed; remote staging was preserved for diagnosis.' }

    Write-Output 'Deployment completed. The service listens only on 127.0.0.1:8787; no public firewall or security-group rule was changed.'
}
finally {
    Pop-Location
    if ($workingDirectory -and (Test-Path -LiteralPath $workingDirectory)) {
        $resolvedWorking = [IO.Path]::GetFullPath($workingDirectory)
        $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if ($resolvedWorking.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolvedWorking) -match '^rag-eval-deploy-[0-9a-f]{32}$') {
            Remove-Item -LiteralPath $resolvedWorking -Recurse -Force
        }
    }
}
