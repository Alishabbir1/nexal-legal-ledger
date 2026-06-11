<#
.SYNOPSIS
    Builds the Nexal Legal Windows application and installer.
.DESCRIPTION
    Single-command build pipeline:
    1. Install Python dependencies
    2. Run quick import verification
    3. Build standalone .exe via PyInstaller
    4. Run build verification test
    5. Create Windows installer via Inno Setup (if available)
.EXAMPLE
    .\build.ps1
    .\build.ps1 -SkipDeps
    .\build.ps1 -SkipTests
#>

param(
    [switch]$SkipDeps,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$distPath = Join-Path $projectRoot "dist_build"
$exeName = "NexalLegal.exe"

Write-Host ''
Write-Host '  Nexal Legal - Build Pipeline' -ForegroundColor Cyan
Write-Host '  ==========================================' -ForegroundColor Cyan
Write-Host ''

# ---------- Step 1: Dependencies ----------
if (-not $SkipDeps) {
    Write-Host '[1/5] Installing Python dependencies...' -ForegroundColor Yellow
    $reqFile = Join-Path $projectRoot 'requirements.txt'
    pip install -r $reqFile -q
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
    Write-Host '      Done.' -ForegroundColor Green
} else {
    Write-Host '[1/5] Skipping dependencies (-SkipDeps)' -ForegroundColor Gray
}

# ---------- Step 2: Import verification ----------
Write-Host '[2/5] Verifying critical imports...' -ForegroundColor Yellow
python -c "import flask, reportlab, openpyxl, cryptography, webview; print('All critical imports OK')"
if ($LASTEXITCODE -ne 0) {
    throw 'Critical Python packages are missing. Run: pip install -r requirements.txt'
}
Write-Host '      Done.' -ForegroundColor Green

# ---------- Step 3: PyInstaller build ----------
Write-Host "[3/5] Building $exeName ..." -ForegroundColor Yellow
if (Test-Path $distPath) { Remove-Item $distPath -Recurse -Force }
$buildWork = Join-Path $projectRoot 'build_temp'
Push-Location $projectRoot
try {
    python -m PyInstaller solicitor_ledger.spec --noconfirm --distpath $distPath --workpath $buildWork --clean
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
} finally {
    Pop-Location
}
$exePath = Join-Path $distPath $exeName
if (-not (Test-Path $exePath)) { throw "$exeName was not created at $exePath" }
$exeSize = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
Write-Host "      Done. Output: dist_build\$exeName - $exeSize MB" -ForegroundColor Green

# ---------- Step 4: Build verification ----------
if (-not $SkipTests) {
    Write-Host '[4/5] Running build verification tests...' -ForegroundColor Yellow
    $verifyScript = Join-Path $projectRoot 'verify_build.py'
    python $verifyScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host '      BUILD VERIFICATION FAILED - see output above' -ForegroundColor Red
        throw 'Build verification failed. Fix the errors and rebuild.'
    }
    Write-Host '      Done. All tests passed.' -ForegroundColor Green
} else {
    Write-Host '[4/5] Skipping tests (-SkipTests)' -ForegroundColor Gray
}

# ---------- Step 5: Installer ----------
Write-Host '[5/5] Building Windows installer...' -ForegroundColor Yellow
$isccPaths = @(
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
)
$iscc = $isccPaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
    $issFile = Join-Path $projectRoot 'installer.iss'
    & $iscc $issFile
    if ($LASTEXITCODE -eq 0) {
        $installerPath = Join-Path $projectRoot 'installer_output\NexalLegalSetup.exe'
        if (Test-Path $installerPath) {
            $instSize = [math]::Round((Get-Item $installerPath).Length / 1MB, 1)
            Write-Host "      Done. Installer: installer_output\NexalLegalSetup.exe - $instSize MB" -ForegroundColor Green
        }
    } else {
        Write-Host '      Inno Setup compilation failed.' -ForegroundColor Red
    }
} else {
    Write-Host '      Inno Setup 6 not found - skipping installer creation.' -ForegroundColor Yellow
    Write-Host '      Install from: https://jrsoftware.org/isinfo.php' -ForegroundColor Gray
    Write-Host "      The standalone .exe is still usable at: dist_build\$exeName" -ForegroundColor Gray
}

# ---------- Summary ----------
Write-Host ''
Write-Host '  Build complete.' -ForegroundColor Cyan
Write-Host ''
Write-Host "  Standalone .exe:  $projectRoot\dist_build\$exeName" -ForegroundColor White
$installerFile = Join-Path $projectRoot 'installer_output\NexalLegalSetup.exe'
if (Test-Path $installerFile) {
    Write-Host "  Installer:        $projectRoot\installer_output\NexalLegalSetup.exe" -ForegroundColor White
}
Write-Host ''
$localAppData = $env:LOCALAPPDATA
Write-Host "  Data directory:   $localAppData\SolicitorLedger\" -ForegroundColor Gray
Write-Host '  Exports:          ~\Documents\NexalLegal\Exports\' -ForegroundColor Gray
Write-Host "  Logs:             $localAppData\SolicitorLedger\logs\" -ForegroundColor Gray
Write-Host ''
