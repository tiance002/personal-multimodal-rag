[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Bundle,
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$HostName,
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z_][A-Za-z0-9_.-]{0,31}$')][string]$User,
    [ValidateRange(1, 65535)][int]$Port = 22,
    [Parameter(Mandatory = $true)][string]$IdentityFile,
    [string]$PythonExecutable = 'python.exe',
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$bundlePath = (Resolve-Path -LiteralPath $Bundle -ErrorAction Stop).Path
$identityPath = (Resolve-Path -LiteralPath $IdentityFile -ErrorAction Stop).Path
$workDirectory = $null
$bundleLock = $null
$knownHostsPath = Join-Path $env:USERPROFILE '.ssh\known_hosts'
if (-not (Test-Path -LiteralPath $knownHostsPath -PathType Leaf)) {
    throw 'Strict SSH host verification requires an existing known_hosts file.'
}
foreach ($toolName in @('ssh.exe', 'sftp.exe', 'python.exe')) {
    if (-not (Get-Command $toolName -ErrorAction SilentlyContinue)) {
        throw "Required local command is missing: $toolName"
    }
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Push-Location $repositoryRoot
try {
    if ($Apply) {
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        $workDirectory = Join-Path $tempRoot ("rag-eval-upload-" + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $workDirectory | Out-Null
        $sanitizedBundle = Join-Path $workDirectory 'sanitized-bundle.json'
        $validationOutput = & $PythonExecutable -m eval_center.cli sanitize $bundlePath $sanitizedBundle 2>&1
    } else {
        $validationOutput = & $PythonExecutable -m eval_center.cli validate $bundlePath 2>&1
    }
    if ($LASTEXITCODE -ne 0) { throw 'Local bundle validation failed; no remote transfer was attempted.' }
    $validation = ($validationOutput -join "`n") | ConvertFrom-Json
    $expectedStatus = if ($Apply) { 'sanitized' } else { 'valid' }
    if ($validation.status -ne $expectedStatus -or $validation.experiment_id -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') {
        throw 'Local bundle validation returned an unexpected result.'
    }
    if (-not $Apply) {
        Write-Output "Bundle $($validation.experiment_id) is valid. No SSH, SFTP, or remote import was performed. Add -Apply to transfer this sanitized bundle."
        exit 0
    }

    # Hold the exact sanitized file open read-only, denying replacement or writes until SFTP exits.
    $bundleLock = [IO.File]::Open($sanitizedBundle, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $bundlePath = $sanitizedBundle
    $experimentId = $validation.experiment_id
    $remoteTemporary = "/srv/rag-eval/incoming/.$experimentId.json.uploading"
    $remoteBundle = "/srv/rag-eval/incoming/$experimentId.json"
    $batchPath = Join-Path $workDirectory 'upload.batch'
    $localSftpPath = $bundlePath.Replace('\', '/')
    Set-Content -LiteralPath $batchPath -Encoding ascii -Value @(
        "put `"$localSftpPath`" $remoteTemporary",
        "chmod 0640 $remoteTemporary",
        "rename $remoteTemporary $remoteBundle"
    )
    $sshCommon = @(
        '-o', 'BatchMode=yes',
        '-o', 'IdentitiesOnly=yes',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', "UserKnownHostsFile=$knownHostsPath"
    )
    $sftpArguments = @('-P', [string]$Port, '-i', $identityPath, '-b', $batchPath) + $sshCommon + @("$User@$HostName")
    & sftp.exe @sftpArguments
    if ($LASTEXITCODE -ne 0) { throw 'Strict SFTP upload failed; no import was attempted.' }

    $remoteImport = "set -a; . /srv/rag-eval/configs/runtime.env; set +a; runuser -u rag-eval -- env EVAL_CENTER_CODE_SHA=`"`$EVAL_CENTER_CODE_SHA`" EVAL_CENTER_DB=/srv/rag-eval/experiments/registry.sqlite3 PYTHONPATH=/srv/rag-eval/app /usr/bin/python3 -m eval_center.cli import $remoteBundle"
    $sshArguments = @('-p', [string]$Port, '-i', $identityPath) + $sshCommon + @("$User@$HostName", $remoteImport)
    $importOutput = & ssh.exe @sshArguments
    if ($LASTEXITCODE -ne 0) { throw 'Remote import failed. The staged bundle was retained for retry or diagnosis.' }
    $importResult = ($importOutput -join "`n") | ConvertFrom-Json
    if ($importResult.status -notin @('imported', 'unchanged')) { throw 'Remote importer returned an unexpected status; the staged bundle was retained.' }

    $deleteBatch = Join-Path $workDirectory 'cleanup.batch'
    Set-Content -LiteralPath $deleteBatch -Encoding ascii -Value "rm $remoteBundle"
    $cleanupArguments = @('-P', [string]$Port, '-i', $identityPath, '-b', $deleteBatch) + $sshCommon + @("$User@$HostName")
    & sftp.exe @cleanupArguments
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Import succeeded, but the sanitized staged file remains in the server incoming directory.'
    }
    Write-Output "Import $($importResult.status): $experimentId"
}
finally {
    Pop-Location
    if ($bundleLock) { $bundleLock.Dispose() }
    if ($workDirectory -and (Test-Path -LiteralPath $workDirectory)) {
        $resolvedWork = [IO.Path]::GetFullPath($workDirectory)
        $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if ($resolvedWork.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolvedWork) -match '^rag-eval-upload-[0-9a-f]{32}$') {
            Remove-Item -LiteralPath $resolvedWork -Recurse -Force
        }
    }
}
