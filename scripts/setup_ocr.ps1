param([string]$Destination = (Join-Path (Get-Location) 'var\tessdata'))

$ErrorActionPreference = 'Stop'
$files = @(
    @{name='eng'; sha256='7D4322BD2A7749724879683FC3912CB542F19906C83BCC1A52132556427170B2'},
    @{name='chi_sim'; sha256='A5FCB6F0DB1E1D6D8522F39DB4E848F05984669172E584E8D76B6B3141E1F730'}
)
$revision = '87416418657359cb625c412a48b6e1d6d41c29bd'
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
foreach ($item in $files) {
    $target = Join-Path $Destination ($item.name + '.traineddata')
    if (-not (Test-Path -LiteralPath $target)) {
        $temporary = "$target.download"
        try {
            Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/$revision/$($item.name).traineddata" -OutFile $temporary
            $actual = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash
            if ($actual -ne $item.sha256) { throw "OCR language data hash mismatch: $($item.name)" }
            Move-Item -LiteralPath $temporary -Destination $target
        } finally {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
        }
    }
    $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
    if ($actual -ne $item.sha256) { throw "OCR language data hash mismatch: $target" }
    Write-Output "$($item.name): $actual"
}
exit 0
