# Purpose: Helpers shared by the docs/test-plan.md case modules and their fixtures.
# What the code does:
#   - make_scratch(folder) builds a private index database in folder and returns a namespace with
#     db, service, searcher, run(roots, include_subdirectories, exclude_patterns, **kwargs) to scan
#     and index, and names(query, **options) returning the matching file names.
# Usage notes, dependencies, or assumptions:
#   - The caller owns the database and must close scratch.db when finished.

from types import SimpleNamespace

from doc_searcher.indexing.service import IndexingService, IndexRequest
from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.storage.database import Database


def make_scratch(folder):
    """A private, empty index in folder: run(roots, ...) scans and indexes; names(q) -> file names."""
    db = Database(str(folder / "scratch.db"))
    service = IndexingService(db)
    searcher = DocumentSearcher(db)

    def run(roots, include_subdirectories=True, exclude_patterns=None, **kwargs):
        request = IndexRequest(
            [str(r) for r in roots],
            include_subdirectories=include_subdirectories,
            exclude_patterns=exclude_patterns or [],
        )
        return service.run(request, **kwargs)

    def names(query, **options):
        return {i.filename for i in searcher.search(query, limit=500, **options)}

    return SimpleNamespace(db=db, service=service, searcher=searcher, run=run, names=names)
