@echo off
REM Runner invoked by Windows Task Scheduler.
REM Do NOT run this manually — it's called by the "Foreign Liquidity Scraper - Daily 14:00" task.
REM All stdout + stderr is appended to scraper_task.log in this folder.

setlocal
cd /d "%~dp0"
echo. >> scraper_task.log
echo ================================================== >> scraper_task.log
echo [%date% %time%] Starting scraper run... >> scraper_task.log
echo ================================================== >> scraper_task.log
python foreign_liquidity_scraper.py >> scraper_task.log 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] Finished with exit code %RC% >> scraper_task.log
endlocal & exit /b %RC%
