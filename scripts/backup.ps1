param(
    [string]$TargetFile,
    [string]$DatabaseName,
    [string]$StorageRoot,
    [string]$OutputDir,
    [string]$Python,
    [string]$ConfirmTarget,
    [switch]$QuiescentConfirmed,
    [switch]$Execute
)
# No compose startup, default database, relative storage, overwrite or cleanup.
$ErrorActionPreference = 'Stop'
if (@($TargetFile,$DatabaseName,$StorageRoot,$Python,$OutputDir) | Where-Object { [string]::IsNullOrWhiteSpace($_) }) {
    Write-Output '{"status":"BLOCKED","error_code":"EXPLICIT_PARAMETERS_REQUIRED"}'
    exit 2
}
$toolArgs = @('-I','-B',(Join-Path $PSScriptRoot 'native_release.py'),'backup',
    '--target-file',$TargetFile,'--database-name',$DatabaseName,
    '--storage-root',$StorageRoot,'--output-dir',$OutputDir)
if ($Execute) { $toolArgs += @('--execute','--confirm-target',$ConfirmTarget) }
if ($QuiescentConfirmed) { $toolArgs += '--quiescent-confirmed' }
& $Python @toolArgs
exit $LASTEXITCODE
