"""Shared fixtures for the docs/test-plan.md case modules: the dataset, built and indexed once."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from doc_searcher.indexing.indexer import DocumentIndexer
from doc_searcher.indexing.scanner import FileScanner
from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.storage.database import Database
from search_plan import dataset
from search_plan.helpers import make_scratch


@pytest.fixture(scope="session")
def dataset_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("plan") / "dataset"
    dataset.build_dataset(root)
    return root


@pytest.fixture(scope="session")
def env(dataset_root, tmp_path_factory):
    """The whole dataset indexed once: root, db, searcher, search() -> paths, results() -> items."""
    db = Database(str(tmp_path_factory.mktemp("plan-db") / "index.db"))
    files = FileScanner().scan_directories([str(dataset_root)])
    DocumentIndexer(db).run_batch_indexing([f[0] for f in files], [])
    searcher = DocumentSearcher(db)

    def results(query, **options):
        items = searcher.search(query, limit=500, **options)
        return {Path(item.path).relative_to(dataset_root).as_posix(): item for item in items}

    yield SimpleNamespace(
        root=dataset_root,
        db=db,
        searcher=searcher,
        results=results,
        search=lambda query, **options: set(results(query, **options)),
    )
    db.close()


@pytest.fixture(scope="session")
def dataset_search(env):
    """search(query, **options) -> set of dataset-relative paths, over the fully indexed dataset."""
    return env.search


@pytest.fixture
def scratch(tmp_path):
    """A private, empty index: run(roots, ...) scans and indexes; names(q) -> file names."""
    scratch = make_scratch(tmp_path)
    yield scratch
    scratch.db.close()
