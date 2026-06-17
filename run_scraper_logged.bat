@echo off
REM ====================================================================
REM  run_scraper_logged.bat
REM  ------------------------------------------------------------------
REM  Task Scheduler wrapper for the Foreign Liquidity scraper.
REM ====================================================================

REM === HARDCODED PATHS (double quotes everywhere) ===
set "WORK_DIR=C:\Users\Hadi-Alghanim\Desktop\New folder\inflation_index"
set "SCRIPT=C:\Users\Hadi-Alghanim\Desktop\New folder\inflation_index\foreign_liquidity_scraper.py"
set "LOG=C:\Users\Hadi-Alghanim\Desktop\New folder\inflation_index\scraper_log.txt"

REM ----- Python Path injected here -----
REM Stable per-user alias (survives Store updates, unlike the version-pinned
REM path under C:\Program Files\WindowsApps).
set "PY_EXE=C:\Users\Hadi-Alghanim\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe"

REM === STEP 1: write "Starting run" to the log IMMEDIATELY ===
echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo [%date% %time%] Starting run >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo Working dir : %WORK_DIR% >> "%LOG%"
echo Script      : %SCRIPT% >> "%LOG%"
echo Python exe  : %PY_EXE% >> "%LOG%"

REM === STEP 2: verify the Python interpreter is real ===
if not exist "%PY_EXE%" (
    echo [%date% %time%] FATAL: Python not found at %PY_EXE% >> "%LOG%"
    echo FATAL: Python not found at %PY_EXE%
    pause
    exit /b 4
)

REM === STEP 3: verify the scraper script exists ===
if not exist "%SCRIPT%" (
    echo [%date% %time%] FATAL: scraper script not found: %SCRIPT% >> "%LOG%"
    echo FATAL: scraper script not found at %SCRIPT%
    pause
    exit /b 3
)

REM === STEP 4: cd into the project folder ===
cd /d "%WORK_DIR%"
if errorlevel 1 (
    echo [%date% %time%] FATAL: cannot cd into %WORK_DIR% >> "%LOG%"
    echo FATAL: cannot cd into %WORK_DIR%
    pause
    exit /b 2
)

REM === STEP 5: keep Python output sane ===
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

REM === STEP 6: log Python version ===
echo --- Python version --- >> "%LOG%"
"%PY_EXE%" --version >> "%LOG%" 2>&1

REM === STEP 7: run the scraper ===
echo --- Scraper output --- >> "%LOG%"
"%PY_EXE%" "%SCRIPT%" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

REM === STEP 8: footer ===
echo --- End of run --- >> "%LOG%"
if %RC% EQU 0 (
    echo [%date% %time%] OK: scraper exited cleanly. >> "%LOG%"
) else (
    echo [%date% %time%] FAILED: exit code %RC% >> "%LOG%"
)

REM === STEP 9: keep the window open ===
echo.
echo ----------------------------------------------------------
echo Done. Exit code: %RC%
echo Log file: %LOG%
echo ----------------------------------------------------------
echo.
pause

exit /b %RC%