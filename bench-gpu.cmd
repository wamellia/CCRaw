@echo off
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo Benchmark: available GPU devices vs CPU on the CCRaw pixel pipeline.
echo.
"%PY%" tools\bench_devices.py build\bench-devices.json
echo.
echo Result saved to build\bench-devices.json
