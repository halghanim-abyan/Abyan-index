# setup_scheduled_task.ps1
# Registers a Windows Scheduled Task that runs foreign_liquidity_scraper.py
# every day at 14:00 (2:00 PM). Safe to re-run — recreates the task idempotently.
#
# Usage (from PowerShell, in this folder):
#     powershell -ExecutionPolicy Bypass -File .\setup_scheduled_task.ps1
#
# Or just double-click setup_scheduled_task.bat.

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
$TaskName    = 'Foreign Liquidity Scraper - Daily 14:00'
$ScriptName  = 'foreign_liquidity_scraper.py'
$WorkingDir  = 'C:\Users\Hadi-Alghanim\Desktop\New folder\inflation_index'
$RunTime     = '14:00'
$RunnerBat   = '_run_scraper_task.bat'
$LogFile     = 'scraper_task.log'

Write-Host ''
Write-Host '=== Foreign Liquidity Scraper - Scheduled Task Setup ===' -ForegroundColor Cyan
Write-Host ''

# ---------------------------------------------------------------------------
# 1) Verify target script + runner both exist
# ---------------------------------------------------------------------------
$ScriptPath = Join-Path $WorkingDir $ScriptName
$RunnerPath = Join-Path $WorkingDir $RunnerBat

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Host "ERROR: Target script not found." -ForegroundColor Red
    Write-Host "       Expected: $ScriptPath" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $RunnerPath)) {
    Write-Host "ERROR: Runner batch file not found." -ForegroundColor Red
    Write-Host "       Expected: $RunnerPath" -ForegroundColor Red
    Write-Host "       (Should have been installed alongside this script.)" -ForegroundColor Red
    exit 1
}

Write-Host "Target script : $ScriptPath" -ForegroundColor Gray
Write-Host "Runner        : $RunnerPath" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# 2) Resolve python.exe (for a friendly pre-flight check only — the runner
#    itself just calls `python`, relying on PATH at task run time).
# ---------------------------------------------------------------------------
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "ERROR: 'python' not found on PATH. Install Python or fix PATH, then retry." -ForegroundColor Red
    exit 1
}
Write-Host "Python exe    : $($python.Source)" -ForegroundColor Gray
Write-Host "Working dir   : $WorkingDir" -ForegroundColor Gray
Write-Host "Run time      : daily at $RunTime" -ForegroundColor Gray
Write-Host "Log file      : $WorkingDir\$LogFile" -ForegroundColor Gray
Write-Host ''

# ---------------------------------------------------------------------------
# 3) Remove existing task with same name (idempotent re-install)
# ---------------------------------------------------------------------------
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host 'Existing task found - removing it first...' -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# ---------------------------------------------------------------------------
# 4) Build the task
#    Action: invoke the runner .bat, with the folder as WorkingDirectory.
#    New-ScheduledTaskAction quotes the -Execute path for us — spaces OK.
# ---------------------------------------------------------------------------
$action = New-ScheduledTaskAction `
    -Execute $RunnerPath `
    -WorkingDirectory $WorkingDir

$trigger = New-ScheduledTaskTrigger -Daily -At $RunTime

# Run as the current user, only when logged on, without requiring admin.
$principal = New-ScheduledTaskPrincipal `
    -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

# ---------------------------------------------------------------------------
# 5) Register
# ---------------------------------------------------------------------------
Register-ScheduledTask `
    -TaskName    $TaskName `
    -Action      $action `
    -Trigger     $trigger `
    -Principal   $principal `
    -Settings    $settings `
    -Description 'Runs the Tadawul Foreign Liquidity scraper once per day at 2:00 PM.' | Out-Null

# ---------------------------------------------------------------------------
# 6) Verify & print next run time
# ---------------------------------------------------------------------------
$task = Get-ScheduledTask -TaskName $TaskName
$info = $task | Get-ScheduledTaskInfo

Write-Host 'Task registered successfully.' -ForegroundColor Green
Write-Host ''
Write-Host ('  Name        : {0}' -f $task.TaskName)
Write-Host ('  State       : {0}' -f $task.State)
Write-Host ('  Next run at : {0}' -f $info.NextRunTime)
Write-Host ''
Write-Host 'Useful commands:' -ForegroundColor Cyan
Write-Host ('  Run now     : Start-ScheduledTask -TaskName "{0}"' -f $TaskName)
Write-Host ('  Inspect     : Get-ScheduledTaskInfo -TaskName "{0}"' -f $TaskName)
Write-Host ('  Disable     : Disable-ScheduledTask -TaskName "{0}"' -f $TaskName)
Write-Host ('  Remove      : Unregister-ScheduledTask -TaskName "{0}" -Confirm:$false' -f $TaskName)
Write-Host ''
Write-Host ('Output will be appended to: {0}\{1}' -f $WorkingDir, $LogFile) -ForegroundColor Gray
Write-Host ''
