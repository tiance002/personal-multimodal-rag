param(
    [ValidateSet('Schema','Verify-Existing','Execute-Native','Retrieval','Quality')][string]$Mode='Schema',
    [string]$Python=(Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Report='', [string]$Dataset='', [string]$Manifest='', [string]$ManifestSha256='',
    [string[]]$Results=@(), [string[]]$ResultSha256=@(),
    [switch]$AllowIsolatedRead, [switch]$AllowIsolatedMockExecution
)
$ErrorActionPreference='Stop'
if (!(Test-Path -LiteralPath $Python)) { throw "Python runtime not found: $Python" }
$root=Split-Path -Parent $PSScriptRoot
if ($Mode -eq 'Schema' -and !$Report) { $Report=Join-Path $root ('var/weknora-port/entry-checks/eval-schema-'+[Guid]::NewGuid().ToString('N')+'.json') }
$toolArgs=@('-B',(Join-Path $PSScriptRoot 'weknora_port_verify_entry.py'),'eval','--mode',$Mode.ToLowerInvariant())
if ($Report) { $toolArgs+=@('--output',$Report) }
if ($Dataset) { $toolArgs+=@('--dataset',$Dataset) }
if ($Manifest) { $toolArgs+=@('--manifest',$Manifest) }
if ($ManifestSha256) { $toolArgs+=@('--manifest-sha256',$ManifestSha256) }
if ($Results.Count) { $toolArgs+=@('--results')+$Results }
if ($ResultSha256.Count) { $toolArgs+=@('--result-sha256')+$ResultSha256 }
if ($AllowIsolatedRead) { $toolArgs+='--allow-isolated-read' }
if ($AllowIsolatedMockExecution) { $toolArgs+='--allow-isolated-mock-execution' }
& $Python @toolArgs
exit $LASTEXITCODE
