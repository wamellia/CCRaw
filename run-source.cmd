@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py %*
) else if exist ".venv-release\Scripts\python.exe" (
    ".venv-release\Scripts\python.exe" main.py %*
) else (
    py -3.12 main.py %*
)
