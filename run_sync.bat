@echo off
REM run_sync.bat — mirror local SQLite -> cloud Postgres after the daily jobs.
REM Safe: reads SQLite only; Postgres failure never affects local data.
REM Schedule this AFTER the 15:00 foreign-liquidity job (e.g. 15:45).
cd /d "%~dp0"
echo ==== sync_to_pg %DATE% %TIME% ==== >> sync_to_pg.log
python sync_to_pg.py >> sync_to_pg.log 2>&1
echo exit=%ERRORLEVEL% >> sync_to_pg.log
