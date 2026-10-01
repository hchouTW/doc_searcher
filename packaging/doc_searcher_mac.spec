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
SRC = os.path.join(ROOT, 'src')
PACKAGE = os.path.join(SRC, 'doc_searcher')
sys.path.insert(0, SRC)  # build-time only: lets collect_submodules import doc_searcher

with open(os.path.join(PACKAGE, 'version.py'), encoding='utf-8') as f:
    APP_VERSION = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', f.read()).group(1)

datas = [
    (os.path.join(PACKAGE, 'assets'), 'assets'),
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
# The MCP server is a separate entry point and is not bundled into the desktop app.
hiddenimports += collect_submodules(
    'doc_searcher', filter=lambda name: not name.startswith('doc_searcher.integrations')
)

# Dense search is opt-in for packaged builds; model files remain user-managed locally.
package_semantic = os.environ.get('DOC_SEARCHER_PACKAGE_SEMANTIC') == '1'
if package_semantic:
    hiddenimports += [
        'sentence_transformers', 'sentence_transformers.models.Transformer',
        'sentence_transformers.models.Pooling', 'sentence_transformers.models.Normalize',
        'transformers.models.xlm_roberta',
    ]
hiddenimports += ['watchdog.observers.polling', 'PIL.Image', 'PIL.TiffImagePlugin']

a = Analysis(
    [os.path.join(PACKAGE, '__main__.py')],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'IPython'] + ([] if package_semantic else ['scipy', 'torch', 'sentence_transformers']),
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
    icon=os.path.join(PACKAGE, 'assets', 'app_icon.png'),
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
