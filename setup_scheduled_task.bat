@echo off
REM One-click wrapper that runs setup_scheduled_task.ps1 with ExecutionPolicy
REM bypass, so you don't have to configure PowerShell policies manually.
REM Double-click this file to register (or re-register) the daily 14:00 task.

setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_scheduled_task.ps1"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo [OK] Scheduled task setup finished.
) else (
    echo [FAIL] Setup exited with code %RC%.
)
echo.
pause
endlocal
