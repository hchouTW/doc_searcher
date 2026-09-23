# Purpose: core package marker.
# What the code does:
#   - Nothing; kept free of imports so importing a single submodule (e.g. core.version)
#     does not pull in Qt, SQLite, parsers, or jieba.
# Usage notes, dependencies, or assumptions:
#   - Import submodules directly, e.g. `from core.<module> import <name>`.
