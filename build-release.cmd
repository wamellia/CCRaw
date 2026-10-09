@echo off
setlocal
cd /d "%~dp0"
echo CCRaw release build: dependency and asset verification, tests, frozen smoke test, portable ZIP.
echo An installer is built only when an Inno Setup compiler is supplied with -Iscc.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build_release.ps1" %*
set CODE=%ERRORLEVEL%
echo.
if %CODE%==0 (echo Build finished successfully.) else (echo Build FAILED - inspect the command output.)
exit /b %CODE%
