"""Logging destinations and stable error categories (Task 4.3)."""

import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from doc_searcher.platform import platform_helper
from doc_searcher.platform.os_detector import detect_os
from doc_searcher.search.searcher import DocumentSearcher, SearchQueryError
from doc_searcher.storage.database import Database
from doc_searcher.storage.errors import DatabaseLockedError

SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def _broken_pdf(path):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "hello world")
    raw = doc.tobytes()
    doc.close()
    # Opens fine, but MuPDF reports "object is not a stream" while extracting the page.
    path.write_bytes(raw.replace(b"stream", b"strean", 1))


def test_cli_stdout_holds_only_results_even_when_mupdf_complains(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.txt").write_text("budget keyword", encoding="utf-8")
    _broken_pdf(docs / "broken.pdf")
    env = {
        **os.environ,
        "PYTHONPATH": str(SRC_DIR),
        "DOC_SEARCHER_DATA_DIR": str(tmp_path / "data"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "doc_searcher", "--dir", str(docs), "--search", "budget"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert "note.txt" in result.stdout
    assert "MuPDF" not in result.stdout and "[*]" not in result.stdout
    assert "[*] 掃描目錄" in result.stderr
    assert "MuPDF error" in result.stderr


@pytest.mark.parametrize(
    "query, code",
    [
        ('"unclosed phrase', "unpaired_phrase"),
        ("AND budget", "operator_position"),
        ("budget AND OR plan", "repeated_operator"),
        ("filename:", "filename_empty"),
        ('filename:"x', "filename_quotes"),
    ],
)
def test_query_errors_carry_stable_codes(tmp_path, query, code):
    db = Database(str(tmp_path / "index.db"))
    try:
        with pytest.raises(SearchQueryError) as exc:
            DocumentSearcher(db).search(query)
        assert exc.value.code == code
    finally:
        db.close()


def test_regex_error_code(tmp_path):
    db = Database(str(tmp_path / "index.db"))
    try:
        with pytest.raises(SearchQueryError) as exc:
            DocumentSearcher(db).search("(", regex=True)
        assert exc.value.code == "regex_syntax"
    finally:
        db.close()


class _FailingConnection:
    """Wraps a real connection; the FTS MATCH query raises the given error."""

    def __init__(self, conn, error):
        self._conn, self._error = conn, error

    def execute(self, sql, *args):
        if "MATCH ?" in sql and "bm25" in sql:
            raise self._error
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def searcher(tmp_path):
    db = Database(str(tmp_path / "index.db"))
    db.save_document_index(
        str(tmp_path / "a.txt"),
        "txt",
        5,
        1.0,
        [
            {
                "segment_id": "1",
                "segment_type": "section",
                "content": "budget plan",
                "tokenized_content": "budget plan",
            }
        ],
    )
    yield db, DocumentSearcher(db)
    db.close()


def _force(db, monkeypatch, error):
    real = db.get_connection()
    monkeypatch.setattr(db, "get_connection", lambda: _FailingConnection(real, error))


def test_fts_syntax_error_falls_back_to_like_search(searcher, monkeypatch, caplog):
    db, engine = searcher
    _force(db, monkeypatch, sqlite3.OperationalError('fts5: syntax error near "."'))
    with caplog.at_level(logging.INFO, logger="doc_searcher.search.searcher"):
        results = engine.search("budget")
    assert [r.filename for r in results] == ["a.txt"]
    assert "using LIKE search" in caplog.text


def test_locked_database_is_not_hidden_by_the_fallback(searcher, monkeypatch):
    db, engine = searcher
    _force(db, monkeypatch, sqlite3.OperationalError("database is locked"))
    with pytest.raises(DatabaseLockedError):
        engine.search("budget")


@pytest.mark.parametrize(
    "error",
    [sqlite3.DatabaseError("database disk image is malformed"), TypeError("programming error")],
)
def test_other_faults_propagate(searcher, monkeypatch, error):
    db, engine = searcher
    _force(db, monkeypatch, error)
    with pytest.raises(type(error)):
        engine.search("budget")


def test_platform_launch_failures_are_logged_and_reported(tmp_path, monkeypatch, caplog):
    target = tmp_path / "doc.txt"
    target.write_text("x", encoding="utf-8")
    mac = detect_os(system_name="Darwin", release="24.0.0", machine="arm64", mac_version="15.0")

    def missing_program(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "open")

    monkeypatch.setattr(platform_helper.subprocess, "run", missing_program)
    assert platform_helper.open_file_with_default_app(str(target), mac) is False
    assert platform_helper.reveal_in_file_manager(str(target), mac) is False
    assert [r.levelno for r in caplog.records] == [logging.WARNING, logging.WARNING]

    def bug(*args, **kwargs):
        raise TypeError("programming error")

    monkeypatch.setattr(platform_helper.subprocess, "run", bug)
    with pytest.raises(TypeError):
        platform_helper.open_file_with_default_app(str(target), mac)


def test_library_modules_do_not_print():
    offenders = [
        str(path.relative_to(SRC_DIR))
        for path in SRC_DIR.rglob("*.py")
        if path.name not in {"cli.py", "selfcheck.py"}
        and "print(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
