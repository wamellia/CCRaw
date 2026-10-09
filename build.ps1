param([string]$PythonPath='', [switch]$SkipTests)
& (Join-Path $PSScriptRoot 'tools\build_release.ps1') -PythonPath $PythonPath -SkipTests:$SkipTests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
