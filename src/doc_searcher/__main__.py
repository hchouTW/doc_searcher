# Purpose: Allow `python -m doc_searcher` to launch the GUI/CLI.
# What the code does:
#   - Delegates to doc_searcher.cli.main and exits with its return code.
# Usage notes, dependencies, or assumptions:
#   - python -m doc_searcher [--help | --version | --dir DIR --search QUERY]

import sys

from doc_searcher.cli import main

sys.exit(main())
