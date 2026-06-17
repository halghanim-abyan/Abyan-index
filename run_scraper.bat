@echo off
cd /d "C:\Users\Hadi-Alghanim\Desktop\New folder\inflation_index"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "LOG=C:\Users\Hadi-Alghanim\Desktop\New folder\inflation_index\inflation_pipeline.log"

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo [%date% %time%] Starting Daily Inflation Pipeline >> "%LOG%"
echo ============================================================ >> "%LOG%"

python -X utf8 main.py >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

if %RC% EQU 0 (
    echo [%date% %time%] OK: pipeline completed. >> "%LOG%"
) else (
    echo [%date% %time%] FAILED: exit code %RC% >> "%LOG%"
)

exit /b %RC%
