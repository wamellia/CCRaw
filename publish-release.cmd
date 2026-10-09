@echo off
setlocal
cd /d "%~dp0"
echo Upload prepared CCRaw artifacts to an explicitly specified repository and existing tag.
echo A draft release is created by default. Authenticate gh before running this command.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\publish_release.ps1" %*
set CODE=%ERRORLEVEL%
echo.
if %CODE%==0 (echo Release creation completed.) else (echo Release creation FAILED - inspect the command output.)
exit /b %CODE%
