# Purpose: Locate bundled read-only resources (icons, assets) in both source and frozen builds.
# What the code does:
#   - Returns an absolute path under PyInstaller's extraction dir (sys._MEIPASS) when frozen,
#     or under the installed doc_searcher package directory otherwise (assets/ is package data).
# Usage notes, dependencies, or assumptions:
#   - resource_path("assets/app_icon.png"); paths must match the `datas` entries in the .spec files.
#   - Only for read-only bundled files; user data belongs in doc_searcher.config's data directory.

import os
import sys

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(relative_path: str) -> str:
    """Resolve a bundled resource path for source runs and PyInstaller builds."""
    base = getattr(sys, "_MEIPASS", _PACKAGE_ROOT)
    return os.path.join(base, relative_path)
