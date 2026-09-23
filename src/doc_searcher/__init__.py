# Purpose: DocSearcher top-level package.
# What the code does:
#   - Exposes __version__ only; importing the package loads no Qt, SQLite, parsers, or jieba.
# Usage notes, dependencies, or assumptions:
#   - Entry points: doc_searcher.cli:main (GUI/CLI) and
#     doc_searcher.integrations.mcp_server:main (MCP stdio server).

from doc_searcher.version import APP_VERSION as __version__

__all__ = ["__version__"]
