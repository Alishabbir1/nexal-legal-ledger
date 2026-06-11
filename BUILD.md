# Nexal Legal — Windows Build & Deployment Guide

## Quick Build

```powershell
# From the project root (c:\solicitor-web):
.\build.ps1
```

This runs the full pipeline:
1. Installs Python dependencies
2. Verifies critical imports
3. Builds `NexalLegal.exe` via PyInstaller
4. Runs the build verification test (all report exports)
5. Creates `NexalLegalSetup.exe` via Inno Setup (if installed)

### Build Options

```powershell
.\build.ps1 -SkipDeps    # Skip pip install (faster rebuild)
.\build.ps1 -SkipTests   # Skip verification tests
```

## Prerequisites

| Tool | Required | Install |
|------|----------|---------|
| Python 3.10+ | Yes | https://python.org |
| pip packages | Yes | `pip install -r requirements.txt` |
| Inno Setup 6 | For installer | https://jrsoftware.org/isinfo.php |

## Build Outputs

| File | Location | Description |
|------|----------|-------------|
| Standalone .exe | `dist_build\NexalLegal.exe` | Runnable without installer |
| Windows Installer | `installer_output\NexalLegalSetup.exe` | Full installer with shortcuts |

## Installation

### From Installer
1. Run `NexalLegalSetup.exe`
2. Follow the wizard (no admin rights required)
3. Choose desktop shortcut and auto-start options
4. Launch from Desktop or Start Menu

### From Standalone .exe
1. Copy `NexalLegal.exe` to any folder
2. Double-click to run
3. Data is stored in `%LOCALAPPDATA%\SolicitorLedger\` regardless of exe location

## File Locations (Production)

All user data is stored under the current user's AppData directory:

```
%LOCALAPPDATA%\SolicitorLedger\
├── solicitor_ledger.db              # SQLite database
├── logs\
│   └── solicitor_ledger.log         # Application log (rotated, 5MB max)
└── backups\
    └── local\                       # Encrypted backup files (.enc)
```

Exported reports are saved where users can easily find them:

```
%USERPROFILE%\Documents\NexalLegal\
└── Exports\
    ├── ledger_report_20260310_2005.pdf
    ├── cashbook_report_20260310_2005.csv
    ├── office_income_20260310_2005.xlsx
    └── ...
```

## Report Exports

All reports can be exported in three formats:

| Format | Library | Extension |
|--------|---------|-----------|
| PDF | ReportLab | `.pdf` |
| CSV | Python stdlib | `.csv` |
| Excel | openpyxl | `.xlsx` |

Available reports:
- Client Ledger Report
- Cashbook Report
- Office Income Summary
- Office Expenses Summary
- Net Profit Report
- SRA Compliance Pack (ZIP)

## Backup System

Backups are AES-256 encrypted and stored in:
- Local: `%LOCALAPPDATA%\SolicitorLedger\backups\local\`
- OneDrive: Auto-detected if available
- USB: When connected

The encryption key is auto-generated on first backup and stored in the database `system_config` table.

## Logging

Application logs are written to:
```
%LOCALAPPDATA%\SolicitorLedger\logs\solicitor_ledger.log
```

Logged events include:
- Application startup/shutdown
- Report generation and export
- Export errors
- Database errors
- Backup operations
- Authentication events

Logs rotate automatically at 5 MB (3 backups kept).

## Troubleshooting

### App won't start
Check the log file at `%LOCALAPPDATA%\SolicitorLedger\logs\solicitor_ledger.log`

### Reports won't export
1. Check that the Exports folder exists: `%USERPROFILE%\Documents\NexalLegal\Exports\`
2. Verify write permissions to the Documents folder
3. Check the application log for errors

### Database issues
The database is at `%LOCALAPPDATA%\SolicitorLedger\solicitor_ledger.db`. It is created automatically on first run.

## Build Verification

The build test (`verify_build.py`) checks:
- All critical Python imports (flask, reportlab, openpyxl, cryptography, webview)
- Database initialization and schema creation
- Client and transaction creation
- All 15 report exports (5 reports × 3 formats)
- File creation and non-zero size

Run manually:
```powershell
python verify_build.py
```
