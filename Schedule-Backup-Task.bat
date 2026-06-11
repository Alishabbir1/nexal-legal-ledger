@echo off
REM Schedule Solicitor Web nightly backup at 02:00
REM Run this as Administrator or with sufficient privileges to create scheduled tasks.

set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%run_backup.py"

REM Use python from PATH, or specify full path if needed
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Python not found in PATH. Please install Python or specify full path.
    exit /b 1
)

set "CMD=pythonw"
where pythonw >nul 2>&1
if %ERRORLEVEL% neq 0 set "CMD=python"

schtasks /create /tn "SolicitorWeb Backup" /tr "\"%CMD%\" \"%SCRIPT%\"" /sc daily /st 02:00 /f /rl HIGHEST
if %ERRORLEVEL% equ 0 (
    echo Scheduled task created: SolicitorWeb Backup runs daily at 02:00
) else (
    echo Failed to create scheduled task. Try running as Administrator.
    exit /b 1
)
pause
