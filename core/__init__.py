# Purpose: Core package initialization.
# What the code does:
#   - Exposes Config, Database, FileScanner, DocumentIndexer, and DocumentSearcher.
# Usage notes, dependencies, or assumptions:
#   - None.

from .config import AppConfig
from .database import Database
from .scanner import FileScanner
from .indexer import DocumentIndexer
from .searcher import DocumentSearcher, SearchResultItem, SegmentMatch
