param(
    [string]$TargetFile,
    [string]$DatabaseName,
    [string]$StorageRoot,
    [string]$InputDir,
    [string]$Python,
    [string]$ConfirmTarget,
    [switch]$Execute
)
# Only an explicit restore-purpose target: different empty DB and new storage.
$ErrorActionPreference = 'Stop'
if (@($TargetFile,$DatabaseName,$StorageRoot,$Python,$InputDir) | Where-Object { [string]::IsNullOrWhiteSpace($_) }) {
    Write-Output '{"status":"BLOCKED","error_code":"EXPLICIT_PARAMETERS_REQUIRED"}'
    exit 2
}
$toolArgs = @('-I','-B',(Join-Path $PSScriptRoot 'native_release.py'),'restore',
    '--target-file',$TargetFile,'--database-name',$DatabaseName,
    '--storage-root',$StorageRoot,'--input-dir',$InputDir)
if ($Execute) { $toolArgs += @('--execute','--confirm-target',$ConfirmTarget) }
& $Python @toolArgs
exit $LASTEXITCODE
