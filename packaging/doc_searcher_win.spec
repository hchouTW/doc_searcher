# -*- mode: python ; coding: utf-8 -*-
# Purpose: PyInstaller specification for the portable single-file DocSearcher.exe (Windows 10/11).
# What the code does:
#   - Builds a onefile, windowed .exe with assets, jieba dictionaries, and all project packages.
#   - Embeds a Windows version resource from src/doc_searcher/version.py; packaging/installer_inno.iss
#     reads the installer version back from it.
# Usage notes, dependencies, or assumptions:
#   - Run via packaging/build_win.ps1, or from the project root:
#     pyinstaller packaging/doc_searcher_win.spec --clean -y
#   - Windows only (the version resource is ignored elsewhere); output: dist/DocSearcher.exe

import os
import re
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

# SPECPATH is injected by PyInstaller; this spec lives in <root>/packaging.
ROOT = os.path.dirname(SPECPATH)
SRC = os.path.join(ROOT, 'src')
PACKAGE = os.path.join(SRC, 'doc_searcher')
sys.path.insert(0, SRC)  # build-time only: lets collect_submodules import doc_searcher

with open(os.path.join(PACKAGE, 'version.py'), encoding='utf-8') as f:
    APP_VERSION = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', f.read()).group(1)

version_tuple = tuple(int(part) for part in APP_VERSION.split('.'))
version_tuple = (version_tuple + (0, 0, 0, 0))[:4]

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('CompanyName', 'Antigravity'),
            StringStruct('FileDescription', 'DocSearcher'),
            StringStruct('FileVersion', APP_VERSION),
            StringStruct('InternalName', 'DocSearcher'),
            StringStruct('OriginalFilename', 'DocSearcher.exe'),
            StringStruct('ProductName', 'DocSearcher'),
            StringStruct('ProductVersion', APP_VERSION),
        ])]),
        VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
    ],
)

# Collect all jieba dictionaries and assets
datas = [
    (os.path.join(PACKAGE, 'assets'), 'assets'),
]
datas += collect_data_files('jieba')

hiddenimports = [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'pymupdf',
    'docx',
    'pptx',
    'openpyxl',
    'xlrd',
    'olefile',
    'jieba',
    'sqlite3',
]
# The MCP server is a separate entry point and is not bundled into the desktop app.
hiddenimports += collect_submodules(
    'doc_searcher', filter=lambda name: not name.startswith('doc_searcher.integrations')
)

a = Analysis(
    [os.path.join(PACKAGE, '__main__.py')],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'torch', 'IPython'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DocSearcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(PACKAGE, 'assets', 'app_icon.ico')],
    version=version_info,
)
