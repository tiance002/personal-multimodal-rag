param(
    [switch]$Fresh,
    [string]$Python,
    [string]$Report,
    [string]$WithServer,
    [string]$HelperPython,
    [string]$TargetFile,
    [string]$DatabaseName,
    [string]$StorageRoot,
    [string]$OutputDir,
    [string]$RestoreTargetFile,
    [string]$RestoreDatabaseName,
    [string]$RestoreStorageRoot
)
# Reject legacy calls before any directory/env/DB/build/service action.
# This entry only constructs an explicit native acceptance plan; no release PASS.
$ErrorActionPreference = 'Stop'
if ($Fresh -or $Report -or $WithServer -or $HelperPython) {
    Write-Output '{"status":"BLOCKED","error_code":"LEGACY_RELEASE_GATE_DISABLED_USE_EXPLICIT_NATIVE_TARGETS"}'
    exit 2
}
$required = @($Python,$TargetFile,$DatabaseName,$StorageRoot,$OutputDir,
    $RestoreTargetFile,$RestoreDatabaseName,$RestoreStorageRoot)
if ($required | Where-Object { [string]::IsNullOrWhiteSpace($_) }) {
    Write-Output '{"status":"BLOCKED","error_code":"EXPLICIT_NATIVE_GATE_PARAMETERS_REQUIRED"}'
    exit 2
}
$toolArgs = @('-I','-B',(Join-Path $PSScriptRoot 'native_release.py'),'gate',
    '--target-file',$TargetFile,'--database-name',$DatabaseName,'--storage-root',$StorageRoot,
    '--output-dir',$OutputDir,'--restore-target-file',$RestoreTargetFile,
    '--restore-database-name',$RestoreDatabaseName,'--restore-storage-root',$RestoreStorageRoot)
& $Python @toolArgs
exit $LASTEXITCODE
