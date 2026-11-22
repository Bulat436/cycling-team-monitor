@echo off
setlocal
where.exe pwsh.exe >nul 2>nul
if errorlevel 1 (
    echo Install PowerShell 7, then open this file again. See the quick guide.
    pause
    exit /b 1
)
pwsh.exe -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0scripts\run-local.ps1"
set "CTM_EXIT=%ERRORLEVEL%"
if not "%CTM_EXIT%"=="0" pause
exit /b %CTM_EXIT%
