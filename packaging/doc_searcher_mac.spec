# -*- mode: python ; coding: utf-8 -*-
# Purpose: PyInstaller specification for the standalone macOS DocSearcher.app bundle.
# What the code does:
#   - Builds a onedir .app (PyInstaller 6 deprecates onefile inside .app bundles) with
#     assets, jieba dictionaries, and all project packages embedded; no Python needed to run.
#   - Converts assets/app_icon.png to .icns automatically (requires Pillow at build time).
# Usage notes, dependencies, or assumptions:
#   - Run via packaging/build_mac.sh, or from the project root:
#     pyinstaller packaging/doc_searcher_mac.spec --clean -y
#   - Builds for the host architecture (arm64 or x86_64); output: dist/DocSearcher.app

import os
import re
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# SPECPATH is injected by PyInstaller; this spec lives in <root>/packaging.
ROOT = os.path.dirname(SPECPATH)
sys.path.insert(0, ROOT)  # so collect_submodules can import the project packages

with open(os.path.join(ROOT, 'core', 'version.py'), encoding='utf-8') as f:
    APP_VERSION = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', f.read()).group(1)

datas = [
    (os.path.join(ROOT, 'assets'), 'assets'),
]
datas += collect_data_files('jieba')

hiddenimports = [
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
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DocSearcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX breaks macOS code signatures
    console=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='DocSearcher',
)

app = BUNDLE(
    coll,
    name='DocSearcher.app',
    icon=os.path.join(ROOT, 'assets', 'app_icon.png'),
    bundle_identifier='com.antigravity.docsearcher',
    version=APP_VERSION,
    info_plist={
        'CFBundleDisplayName': 'DocSearcher',
        'CFBundleShortVersionString': APP_VERSION,
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,  # allow dark mode
        'LSMinimumSystemVersion': '11.0',
    },
)
