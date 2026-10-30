param([switch]$RunTests, [string]$Python = "")
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = if ($Python) { $Python } else { Join-Path $root '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Create .venv and install the project test and build dependencies first.' }
& $python -c "import sys, PyQt6, PyInstaller; assert sys.version_info >= (3,12)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+, PyQt6 and PyInstaller are required in .venv.' }
if ($RunTests) {
    $oldData = $env:ALIENAPE_DATA_DIR
    $tempData = Join-Path ([IO.Path]::GetTempPath()) ('alienape-build-test-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tempData | Out-Null
    try { $env:ALIENAPE_DATA_DIR = $tempData; & $python -m pytest (Join-Path $root 'tests'); if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' } }
    finally { $env:ALIENAPE_DATA_DIR = $oldData }
}
& $python -m PyInstaller --noconfirm --workpath (Join-Path $root 'build') --distpath (Join-Path $root 'dist') (Join-Path $root 'AlienApe.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
Write-Host ('Built application: ' + (Join-Path $root 'dist/AlienApe/AlienApe.exe'))
