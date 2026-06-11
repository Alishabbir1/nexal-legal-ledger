# Nexal Legal - Installer Creator
# Builds the installer package. Run from project root after PyInstaller build.

param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$distExe = Join-Path $projectRoot "dist_build\NexalLegal.exe"

if (-not (Test-Path $distExe)) {
    Write-Host "NexalLegal.exe not found. Running PyInstaller..." -ForegroundColor Yellow
    Push-Location $projectRoot
    python -m PyInstaller solicitor_ledger.spec --noconfirm --distpath (Join-Path $projectRoot "dist_build")
    Pop-Location
}

$installDir = Join-Path $projectRoot "dist_build"
$installerName = "NexalLegal_Setup.exe"

# Create a simple batch-based installer that users can run
$installerScript = @'
@echo off
title Nexal Legal - Installer
echo.
echo  Nexal Legal
echo  ===========
echo.

set "INSTALL_DIR=%LOCALAPPDATA%\NexalLegal"
set "EXE_NAME=NexalLegal.exe"

mkdir "%INSTALL_DIR%" 2>nul
copy /Y "%~dp0NexalLegal.exe" "%INSTALL_DIR%\%EXE_NAME%" >nul

echo Installed to: %INSTALL_DIR%
echo.

:: Create Start Menu shortcut
set "STARTMENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%STARTMENU%\Nexal Legal.lnk'); $s.TargetPath = '%INSTALL_DIR%\%EXE_NAME%'; $s.WorkingDirectory = '%INSTALL_DIR%'; $s.Save()"
echo Start Menu shortcut created.

:: Create Desktop shortcut (uses proper Desktop path - works with OneDrive)
powershell -NoProfile -Command "try { $d = [Environment]::GetFolderPath('Desktop'); if (Test-Path $d) { $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut((Join-Path $d 'Nexal Legal.lnk')); $s.TargetPath = '%INSTALL_DIR%\%EXE_NAME%'; $s.WorkingDirectory = '%INSTALL_DIR%'; $s.Save(); Write-Host 'Desktop shortcut created.' } else { Write-Host 'Desktop shortcut skipped (Desktop folder not found).' } } catch { Write-Host 'Desktop shortcut skipped:' $_.Exception.Message }"

echo.
echo Installation complete. Double-click "Nexal Legal" to run.
echo.
pause
'@

# Write installer batch file
$batchPath = Join-Path $installDir "Install_SolicitorLedger.bat"
$installerScript | Out-File -FilePath $batchPath -Encoding ASCII
Write-Host "Created: $batchPath" -ForegroundColor Green

# Create a zip package for easy distribution
$zipPath = Join-Path $projectRoot "dist_build\NexalLegal_Package.zip"
if (Get-Command Compress-Archive -ErrorAction SilentlyContinue) {
    Compress-Archive -Path $distExe, $batchPath -DestinationPath $zipPath -Force
    Write-Host "Created: $zipPath" -ForegroundColor Green
}

Write-Host "`nTo distribute to 8 laptops:" -ForegroundColor Cyan
Write-Host "  1. Copy the 'dist_build' folder (or the .zip) to each laptop"
Write-Host "  2. Run Install_SolicitorLedger.bat"
Write-Host "  3. Or run NexalLegal.exe directly (no install needed)"
Write-Host ""
