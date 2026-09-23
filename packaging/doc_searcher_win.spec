# -*- mode: python ; coding: utf-8 -*-
# PyInstaller specification file for DocSearcher on Windows 11

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# SPECPATH is injected by PyInstaller; this spec lives in <root>/packaging.
ROOT = os.path.dirname(SPECPATH)
sys.path.insert(0, ROOT)  # so collect_submodules can import the project packages

block_cipher = None

# Collect all jieba dictionaries and assets
datas = [
    (os.path.join(ROOT, 'assets'), 'assets'),
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
hiddenimports += collect_submodules('core')
hiddenimports += collect_submodules('parsers')
hiddenimports += collect_submodules('ui')
hiddenimports += collect_submodules('utils')

a = Analysis(
    [os.path.join(ROOT, 'main.py')],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'torch', 'IPython'],
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
    icon=[os.path.join(ROOT, 'assets', 'app_icon.ico')],
)
