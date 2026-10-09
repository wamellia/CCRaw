param([Parameter(Mandatory=$true)][string]$PythonPath)
$ErrorActionPreference = 'Stop'
# Use a dedicated WindowsML environment prepared with requirements-directml.txt;
# no runtime distribution is uninstalled or replaced by this wrapper.
& $PythonPath -c "import onnxruntime as o; assert 'DmlExecutionProvider' in o.get_available_providers()"
if ($LASTEXITCODE -ne 0) { throw 'A dedicated WindowsML/DirectML environment is required.' }
& (Join-Path $PSScriptRoot 'tools\build_release.ps1') -PythonPath $PythonPath
