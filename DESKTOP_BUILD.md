# Nexal Legal - Desktop Build Guide

This guide explains how to build the Windows desktop application and installer for distribution to the 8 laptops.

## Architecture Overview

| Component | Technology |
|-----------|------------|
| **Embedded server** | Flask (already in use - lightweight, no changes) |
| **Packaging** | PyInstaller (single .exe, ~50–80 MB) |
| **System tray** | pystray |
| **Installer** | Inno Setup (free, creates .exe installer) |
| **Data storage** | SQLite in `%APPDATA%\SolicitorLedger\` |

## Requirements

- Python 3.8+
- Windows 10/11
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) (for creating the installer)

## Build Steps

### Step 1: Install Dependencies

```powershell
cd C:\solicitor-web
pip install -r requirements.txt
```

### Step 2: Test Desktop Launcher (Development)

```powershell
python desktop_launcher.py
```

- A console window will appear briefly.
- A tray icon appears in the system tray (near the clock).
- Right-click the icon: **Open Application** | **Start when Windows starts** | **Quit**.
- Open Application opens http://127.0.0.1:5000 in your browser.

### Step 3: Build the Executable

```powershell
pyinstaller solicitor_ledger.spec
```

This creates `dist\SolicitorLedger.exe` (single self-contained executable).

### Step 4: Create the Installer

1. Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) if not already installed.
2. Open `installer.iss` in Inno Setup Compiler.
3. Build → Compile (or press Ctrl+F9).

Output: `dist\SolicitorLedger_Setup.exe`

### Step 5: Distribute

Copy `dist\SolicitorLedger_Setup.exe` to each of the 8 laptops and run it. The installer will:

- Install to `C:\Users\<user>\AppData\Local\Programs\Nexal Legal\`
- Create Start Menu shortcut
- Optionally add a Desktop shortcut
- Optionally enable auto-start when Windows starts
- Allow launching the app after install

## Features

| Feature | Implementation |
|---------|----------------|
| **Self-contained exe** | PyInstaller bundles Python, Flask, and dependencies |
| **Embedded server** | Flask runs on localhost:5000 in a background thread |
| **System tray** | Tray icon with Open, Auto-start toggle, Quit |
| **Auto-start** | Optional during install; toggle anytime via tray menu |
| **Data persistence** | Database in `%APPDATA%\SolicitorLedger\solicitor_ledger.db` |

## Customization

### Installer (installer.iss)

- `MyAppPublisher`: Change to your firm name.
- `MyAppURL`: Your website or support URL.
- `SetupIconFile`: Add an `.ico` file for the installer and shortcuts.

### Application Icon (optional)

1. Create or obtain a `icon.ico` file (e.g. 256×256).
2. In `solicitor_ledger.spec`, set:
   ```python
   icon='icon.ico'
   ```
3. In `installer.iss`, set:
   ```
   SetupIconFile=icon.ico
   UninstallDisplayIcon={app}\SolicitorLedger.exe
   ```

### Hide Console Window

To run without a console window (tray only):

In `solicitor_ledger.spec`, change:

```python
console=False
```

Note: With `console=False`, errors will not appear in a window. Keep `console=True` during testing.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Module not found" when running .exe | Add the missing module to `hiddenimports` in `solicitor_ledger.spec` |
| Database not found after install | Database is created on first run in `%APPDATA%\SolicitorLedger\` |
| Port 5000 in use | Change `PORT` in `desktop_launcher.py` |
| Antivirus flags the .exe | PyInstaller exes can be flagged; add an exception or sign the exe |

## File Summary

| File | Purpose |
|------|---------|
| `desktop_launcher.py` | Entry point: tray, Flask thread, auto-start |
| `solicitor_ledger.spec` | PyInstaller build configuration |
| `installer.iss` | Inno Setup installer script |
| `database.py` | Uses APPDATA for data when running as exe |
