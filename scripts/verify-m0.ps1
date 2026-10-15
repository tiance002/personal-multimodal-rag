param(
    [string]$Python=(Join-Path (Get-Location) '.venv\Scripts\python.exe'),
    [string]$Report=''
)
# Native development entry checks only; not the historical M0 milestone gate.
$ErrorActionPreference='Stop'
if (!(Test-Path -LiteralPath $Python)) { throw "Python runtime not found: $Python" }
$root=Split-Path -Parent $PSScriptRoot
if (!$Report) { $Report=Join-Path $root ('var/weknora-port/entry-checks/verify-'+[Guid]::NewGuid().ToString('N')+'.json') }
& $Python -B (Join-Path $PSScriptRoot 'weknora_port_verify_entry.py') verify --output $Report
exit $LASTEXITCODE
