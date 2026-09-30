"""Shared fixtures for the docs/test-plan.md case modules: the dataset, built and indexed once."""

from types import SimpleNamespace

import pytest

from doc_searcher.indexing.indexer import DocumentIndexer
from doc_searcher.indexing.scanner import FileScanner
from doc_searcher.indexing.service import IndexingService, IndexRequest
from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.storage.database import Database
from search_plan import dataset


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
    prefix = str(dataset_root) + "/"

    def results(query, **options):
        items = searcher.search(query, limit=500, **options)
        return {item.path.removeprefix(prefix): item for item in items}

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
    """A private, empty index: run(roots, ...) scans and indexes; search(q) -> file names."""
    db = Database(str(tmp_path / "scratch.db"))
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

    yield SimpleNamespace(db=db, service=service, searcher=searcher, run=run, names=names)
    db.close()
