@echo off
REM Serene Solicitors Ledger — Build wrapper
REM Calls build.ps1 via PowerShell

powershell -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
if %ERRORLEVEL% neq 0 (
    echo.
    echo BUILD FAILED — see output above.
    pause
    exit /b 1
)
pause
