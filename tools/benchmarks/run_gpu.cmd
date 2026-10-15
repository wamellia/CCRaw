@echo off
setlocal
cd /d "%~dp0..\.."
set PY=.venv\Scripts\python.exe
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo Benchmark: available GPU devices vs CPU on the CCRaw pixel pipeline.
echo.
"%PY%" tools\benchmarks\bench_devices.py build\bench-devices.json %*
set CODE=%ERRORLEVEL%
echo.
if %CODE%==0 (echo Result saved to build\bench-devices.json) else (echo Benchmark FAILED - inspect the command output.)
exit /b %CODE%
