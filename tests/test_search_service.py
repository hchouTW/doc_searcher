"""Tests for the headless SearchService used by the MCP server."""

import shutil
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import AppConfig
from core.search_service import SearchService, document_uri

SAMPLE_DIR = Path(__file__).parent / "sample_files"


@pytest.fixture
def make_service(tmp_path, monkeypatch):
    """Build a SearchService over an isolated data dir with the given search folders."""
    monkeypatch.setenv("DOC_SEARCHER_DATA_DIR", str(tmp_path / "data"))
    services = []

    def _make(directories):
        config = AppConfig()
        config.directories = [str(d) for d in directories]
        config.save()
        service = SearchService(config)
        services.append(service)
        return service

    yield _make
    for service in services:
        service.wait_for_reindex(30)
        service.db.close()


@pytest.fixture
def docs(tmp_path):
    folder = tmp_path / "docs"
    shutil.copytree(SAMPLE_DIR, folder)
    return folder


def reindex(service, directory=None):
    started = service.start_reindex(directory)
    assert started["status"] == "started"
    service.wait_for_reindex(60)
    status = service.index_status()
    assert status["reindex"]["state"] == "completed", status
    return status


def test_reindex_then_search_returns_plain_snippets(make_service, docs):
    service = make_service([docs])
    status = reindex(service)
    assert status["total_documents"] >= 5
    assert status["directories"] == [str(docs)]
    assert status["last_indexed_at"]

    found = service.search("預算")
    assert found["result_count"] >= 2
    first = found["results"][0]
    assert first["resource_uri"] == document_uri(first["path"])
    snippets = [s for match in first["matches"] for s in match["snippets"]]
    assert snippets and all("<" not in s for s in snippets)
    assert any("**預算**" in s for s in snippets)


def test_search_format_groups_filter_and_merge(make_service, docs):
    service = make_service([docs])
    reindex(service)

    excel = service.search("預算", formats=["excel"])["results"]
    assert excel and {r["file_type"] for r in excel} <= {"xlsx", "xls"}

    merged = service.search("預算", formats=["pdf", "excel"])["results"]
    assert {r["file_type"] for r in merged} >= {"pdf"} | {r["file_type"] for r in excel}
    assert len(service.search("預算", formats=["pdf", "excel"], limit=1)["results"]) == 1


@pytest.mark.parametrize("kwargs", [
    {"query": "   "},
    {"query": "預算", "formats": ["images"]},
    {"query": "預算", "limit": 0},
    {"query": "預算", "limit": 101},
    {"query": "filename:"},
])
def test_search_rejects_invalid_input(make_service, docs, kwargs):
    service = make_service([docs])
    with pytest.raises(ValueError):
        service.search(**kwargs)


def test_reindex_requires_configured_folder(make_service, docs, tmp_path):
    with pytest.raises(ValueError, match="No search folders"):
        make_service([]).start_reindex()

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ValueError, match="not inside a configured search folder"):
        make_service([docs]).start_reindex(str(outside))


def test_subfolder_reindex_leaves_other_folders_indexed(make_service, tmp_path):
    folder_a, folder_b = tmp_path / "a", tmp_path / "b"
    shutil.copytree(SAMPLE_DIR, folder_a)
    shutil.copytree(SAMPLE_DIR, folder_b)
    service = make_service([folder_a, folder_b])
    total = reindex(service)["total_documents"]

    (folder_a / "sample_readme.txt").unlink()
    status = reindex(service, str(folder_a))

    assert status["reindex"]["result"]["deleted"] == 1
    assert status["total_documents"] == total - 1
    assert service.db.get_document_by_path(str(folder_b / "sample_readme.txt"))


def test_second_reindex_while_running_is_rejected(make_service, docs, monkeypatch):
    service = make_service([docs])
    release = threading.Event()
    monkeypatch.setattr(service, "_reindex", lambda targets, scope: release.wait(10) and {})

    assert service.start_reindex()["status"] == "started"
    second = service.start_reindex()
    assert second["status"] == "already_running"
    assert second["reindex"]["state"] == "running"

    release.set()
    service.wait_for_reindex(10)
    assert service.index_status()["reindex"]["state"] == "completed"


def test_document_text_only_serves_indexed_documents(make_service, docs):
    service = make_service([docs])
    reindex(service)

    text = service.document_text(str(docs / "sample_report.pdf"))
    assert text.startswith("# sample_report.pdf")
    assert "## page 1" in text

    with pytest.raises(LookupError):
        service.document_text(str(docs / "missing.pdf"))
    with pytest.raises(LookupError):
        service.document_text(str(SAMPLE_DIR.parent / "test_parsers.py"))
