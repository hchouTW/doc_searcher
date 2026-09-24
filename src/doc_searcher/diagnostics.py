# Purpose: One logging configuration for every DocSearcher entry point.
# What the code does:
#   - configure_logging() sends log records to stderr as "[logger] LEVEL: message", never to
#     stdout, so CLI results and the MCP stdio protocol stay clean.
# Usage notes, dependencies, or assumptions:
#   - Called by cli.main, desktop.app.run_app, and integrations.mcp_server.main; library modules
#     only create loggers with logging.getLogger(__name__).
#   - DOC_SEARCHER_LOG_LEVEL (e.g. INFO, DEBUG) overrides the default WARNING level.
#   - In the windowed Windows build sys.stderr is None; records are then dropped silently.

import logging
import os
import sys


def configure_logging() -> None:
    level_name = os.environ.get("DOC_SEARCHER_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    if sys.stderr is None:
        logging.basicConfig(level=level, handlers=[logging.NullHandler()])
        return
    logging.basicConfig(
        level=level, stream=sys.stderr, format="[%(name)s] %(levelname)s: %(message)s"
    )
