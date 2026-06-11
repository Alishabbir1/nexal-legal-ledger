# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Nexal Legal
# Build with: pyinstaller solicitor_ledger.spec --noconfirm

import os
import glob

block_cipher = None

# ---------- Data files to bundle ----------
datas = [
    # HTML templates (recursive)
    ('templates', 'templates'),
    # Static assets including subdirectories (css/, js/, docs/)
    ('static', 'static'),
]

# Bundle supporting Python modules that are imported at runtime
_py_modules = [
    'app.py', 'database.py', 'date_utils.py', 'backup_service.py', 'backup_scheduler.py',
    'bank_file_parser.py', 'reconciliation_matching.py', 'desktop_api.py',
    'run_backup.py',
]
for mod in _py_modules:
    if os.path.exists(mod):
        datas.append((mod, '.'))

a = Analysis(
    ['desktop_app.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # Flask ecosystem
        'flask', 'flask.json', 'werkzeug', 'werkzeug.serving',
        'werkzeug.security', 'jinja2', 'markupsafe', 'itsdangerous', 'click', 'blinker',
        # PDF generation
        'reportlab', 'reportlab.lib', 'reportlab.lib.colors', 'reportlab.lib.pagesizes',
        'reportlab.lib.styles', 'reportlab.lib.units', 'reportlab.lib.enums',
        'reportlab.platypus', 'reportlab.platypus.doctemplate',
        'reportlab.platypus.tables', 'reportlab.platypus.paragraph',
        'reportlab.platypus.flowables', 'reportlab.pdfbase',
        'reportlab.pdfbase.pdfmetrics', 'reportlab.pdfbase.ttfonts',
        'reportlab.graphics', 'reportlab.rl_config',
        # Excel generation
        'openpyxl', 'openpyxl.styles', 'openpyxl.utils',
        # Document parsing
        'PyPDF2', 'docx',
        # Encryption
        'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.ciphers', 'cryptography.hazmat.backends',
        # Desktop window
        'webview', 'bottle', 'clr',
        # Image / tray
        'PIL', 'PIL.Image', 'PIL._tkinter_finder',
        # tkinter needed by desktop_api.py file dialog
        'tkinter', 'tkinter.filedialog',
        # pkg_resources / setuptools runtime deps
        'jaraco', 'jaraco.functools', 'jaraco.context', 'jaraco.text',
        'platformdirs',
        # Logging
        'logging.handlers',
        'date_utils',
    ],
    hookspath=['pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='NexalLegal',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
