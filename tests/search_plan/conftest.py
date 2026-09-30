"""Shared fixtures for the docs/test-plan.md case modules: the dataset, built and indexed once."""

from pathlib import Path

import pytest

from doc_searcher.indexing.indexer import DocumentIndexer
from doc_searcher.indexing.scanner import FileScanner
from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.storage.database import Database
from search_plan import dataset


@pytest.fixture(scope="session")
def dataset_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("plan") / "dataset"
    dataset.build_dataset(root)
    return root


@pytest.fixture(scope="session")
def dataset_search(dataset_root, tmp_path_factory):
    """search(query, **options) -> set of dataset-relative paths, over the fully indexed dataset."""
    db = Database(str(tmp_path_factory.mktemp("plan-db") / "index.db"))
    files = FileScanner().scan_directories([str(dataset_root)])
    DocumentIndexer(db).run_batch_indexing([f[0] for f in files], [])
    searcher = DocumentSearcher(db)

    def search(query, **options):
        return {
            Path(r.path).relative_to(dataset_root).as_posix()
            for r in searcher.search(query, limit=500, **options)
        }

    yield search
    db.close()
