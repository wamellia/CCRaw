param([string]$PythonPath='', [string]$Iscc='', [string]$AssetArchive='', [switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$PythonPath = if ($PythonPath) { (Resolve-Path -LiteralPath $PythonPath).Path } else { '' }
$AssetArchive = if ($AssetArchive) { (Resolve-Path -LiteralPath $AssetArchive).Path } else { '' }
$Iscc = if ($Iscc) { (Resolve-Path -LiteralPath $Iscc).Path } else { '' }
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $root
function Run([string[]]$Arguments) {
    & $script:python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Build step failed: $($Arguments -join ' ')" }
}
$python = $PythonPath
if (-not $python) { $python = Join-Path $root '.venv-release\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    if ($PythonPath) { throw 'The requested Python interpreter does not exist.' }
    & py -3.12 -m venv (Join-Path $root '.venv-release')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
}
$env:PYTHONUTF8 = '1'
if (-not $PythonPath) { Run @('-m','pip','install','-r','requirements/agent.lock.txt') }
Run @('-c',"import nanobot, tokenizers")
Run @('tools/packaging/check_dependencies.py')
Run @('-m','pip','check')
if ($AssetArchive) { Run @('tools/assets/fetch_assets.py','--archive',$AssetArchive) }
Run @('tools/assets/fetch_assets.py','--verify-only')
Run @('tools/packaging/prepare_agent_models.py')
Run @('-c',"import numexpr; from ccraw import native_kernels; native_kernels.warm(); assert native_kernels.enabled, 'Native preview acceleration unavailable'")
if (-not $SkipTests) {
    $oldQt = $env:QT_QPA_PLATFORM
    try {
        $env:QT_QPA_PLATFORM = 'offscreen'
        Run @('-m','pytest','tests','-q')
    } finally { $env:QT_QPA_PLATFORM = $oldQt }
}
Run @('tools/packaging/collect_licenses.py')
Run @('-m','PyInstaller','--noconfirm','CCRaw.spec')
$smoke = Join-Path $root 'build\release-smoke'
Run @('-c',"from PIL import Image; from pathlib import Path; p=Path('build/release-smoke'); p.mkdir(parents=True,exist_ok=True); Image.new('RGB',(640,480),(100,150,80)).save(p/'sample.png')")
$oldQt = $env:QT_QPA_PLATFORM
$oldData = $env:CCRAW_DATA_DIR
$oldCache = $env:CCRAW_CACHE_DIR
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:CCRAW_DATA_DIR = Join-Path $smoke 'user'
    $env:CCRAW_CACHE_DIR = Join-Path $smoke 'cache'
    $process = Start-Process -FilePath (Join-Path $root 'dist\CCRaw\CCRaw.exe') -ArgumentList @('--smoke-test', ('"'+(Join-Path $smoke 'sample.png')+'"'), ('"'+$smoke+'"')) -PassThru -Wait -WindowStyle Hidden
    if ($process.ExitCode -ne 0) { throw 'Frozen desktop smoke test failed.' }
    $report = Get-Content -LiteralPath (Join-Path $smoke 'report.json') -Raw | ConvertFrom-Json
    if (-not $report.ok -or -not $report.native_preview_acceleration) { throw 'Frozen runtime or native preview acceleration failed.' }
} finally {
    $env:QT_QPA_PLATFORM = $oldQt
    $env:CCRAW_DATA_DIR = $oldData
    $env:CCRAW_CACHE_DIR = $oldCache
}
Run @('tools/packaging/package_windows.py')
if ($Iscc) {
    if (-not (Test-Path -LiteralPath $Iscc -PathType Leaf)) { throw 'Inno Setup compiler not found.' }
    & $Iscc installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
}
Run @('tools/packaging/package_windows.py','--checksums')
Write-Output 'CCRaw build completed locally. Publication is a separate explicit action.'
