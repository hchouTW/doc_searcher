# Purpose: Unit tests for DocumentSearcher full-text search engine.
# What the code does:
#   - Verifies search querying with single and multiple Chinese/English keywords.
#   - Verifies type-based filtering (PDF, Word, Excel, PPT).
#   - Verifies modification-date and file-size metadata filters.
#   - Verifies snippet generation and <mark> tag highlighting.
# Usage notes, dependencies, or assumptions:
#   - Run via pytest.

import os
import sys
import tempfile
import threading
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from core.scanner import FileScanner
from core.indexer import DocumentIndexer
from core.searcher import DocumentSearcher, SearchQueryError

SAMPLE_DIR = Path(__file__).parent / "sample_files"


def test_document_searcher():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_search.db")
        db = Database(db_path)
        indexer = DocumentIndexer(db)

        scanner = FileScanner()
        files = scanner.scan_directories([str(SAMPLE_DIR)])
        file_paths = [f[0] for f in files]
        indexer.run_batch_indexing(file_paths, [])

        searcher = DocumentSearcher(db)

        # 1. Search for single Chinese keyword
        results_budget = searcher.search("預算")
        assert len(results_budget) >= 4
        # Should match pdf, docx, xlsx, xls, pptx, txt
        found_types = {r.file_type for r in results_budget}
        assert "pdf" in found_types
        assert "docx" in found_types
        assert "xlsx" in found_types
        assert "xls" in found_types

        # 2. Check Snippet and highlighting
        first_res = results_budget[0]
        assert len(first_res.segments) > 0
        snippet = first_res.segments[0].snippets[0]
        assert "<mark" in snippet
        assert "預算" in snippet

        # 3. Multiple keywords AND search
        results_multi = searcher.search("專案 2026")
        assert len(results_multi) >= 2
        for r in results_multi:
            # Check snippet content has matched terms
            all_snips = " ".join([s for seg in r.segments for s in seg.snippets])
            assert "<mark" in all_snips

        # 4. Filter by file type
        pdf_only = searcher.search("預算", type_filter="pdf")
        assert len(pdf_only) == 1
        assert pdf_only[0].file_type == "pdf"

        # Excel filter matches both xlsx and xls
        excel_only = searcher.search("預算", type_filter="excel")
        assert len(excel_only) == 2
        assert {r.file_type for r in excel_only} == {"xlsx", "xls"}

        # 5. Phrase query
        phrase_results = searcher.search('"保密協定條款"')
        assert len(phrase_results) >= 2
        phrase_types = {r.file_type for r in phrase_results}
        assert "docx" in phrase_types
        assert "pptx" in phrase_types

        # 6. Non-matching query
        no_results = searcher.search("完全不可能存在的隨機字詞XYZ9999")
        assert len(no_results) == 0

        db.close()


def test_search_can_run_while_index_is_being_updated(tmp_path):
    db = Database(str(tmp_path / "concurrent_search.db"))
    document_path = str(tmp_path / "concurrent.txt")
    errors = []
    start = threading.Barrier(2)

    def save_version(version: int):
        db.save_document_index(
            file_path=document_path,
            file_type="txt",
            file_size=version,
            mtime=float(version),
            segments=[{
                "segment_id": "1",
                "segment_type": "text",
                "content": f"budget version {version}",
                "tokenized_content": f"budget version {version}",
            }],
        )

    save_version(0)

    def update_index():
        try:
            start.wait()
            for version in range(1, 31):
                save_version(version)
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    def search_repeatedly():
        try:
            searcher = DocumentSearcher(db)
            start.wait()
            for _ in range(30):
                assert searcher.search("budget")
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    writer = threading.Thread(target=update_index)
    reader = threading.Thread(target=search_repeatedly)
    writer.start()
    reader.start()
    writer.join(timeout=10)
    reader.join(timeout=10)

    assert writer.is_alive() is False
    assert reader.is_alive() is False
    assert errors == []
    db.close()


def test_boolean_phrase_and_filename_search_features(tmp_path):
    db = Database(str(tmp_path / "advanced_search.db"))

    def save(name: str, file_type: str, content: str, tokenized: str):
        db.save_document_index(
            file_path=str(tmp_path / name),
            file_type=file_type,
            file_size=len(content),
            mtime=1.0,
            segments=[{
                "segment_id": "1",
                "segment_type": "text",
                "content": content,
                "tokenized_content": tokenized,
            }],
        )

    save("public.txt", "txt", "alpha public", "alpha public")
    save("secret.txt", "txt", "alpha secret", "alpha secret")
    save("ordered.txt", "txt", "alpha beta", "alpha beta")
    save("separated.txt", "txt", "alpha middle beta", "alpha middle beta")
    save("annual_report_2026.pdf", "pdf", "financial", "financial")
    save("annualXreport_2026.pdf", "pdf", "financial", "financial")

    searcher = DocumentSearcher(db)

    not_results = searcher.search("alpha NOT secret")
    assert {result.filename for result in not_results} == {
        "public.txt",
        "ordered.txt",
        "separated.txt",
    }

    or_results = searcher.search("secret OR financial")
    assert {result.filename for result in or_results} == {
        "secret.txt",
        "annual_report_2026.pdf",
        "annualXreport_2026.pdf",
    }

    phrase_results = searcher.search('"alpha beta"')
    assert {result.filename for result in phrase_results} == {"ordered.txt"}

    filename_results = searcher.search("FILENAME:ANNUAL_REPORT", type_filter="pdf")
    assert [result.filename for result in filename_results] == [
        "annual_report_2026.pdf"
    ]
    assert filename_results[0].segments[0].segment_type == "檔名"
    assert searcher.search("檔名:public")[0].filename == "public.txt"
    db.close()


@pytest.mark.parametrize(
    "query",
    [
        '"未結束片語',
        "AND 預算",
        "預算 OR",
        "預算 AND OR 決算",
        'filename:"未結束檔名',
        "檔名:",
    ],
)
def test_invalid_search_syntax_has_user_friendly_error(tmp_path, query):
    db = Database(str(tmp_path / "invalid_query.db"))
    searcher = DocumentSearcher(db)

    with pytest.raises(SearchQueryError):
        searcher.search(query)

    db.close()


def test_search_metadata_filters_apply_to_content_and_filename_queries(tmp_path):
    db = Database(str(tmp_path / "metadata_filters.db"))

    def save(name, size, mtime):
        db.save_document_index(
            file_path=str(tmp_path / name),
            file_type="txt",
            file_size=size,
            mtime=mtime,
            segments=[{
                "segment_id": "1",
                "segment_type": "text",
                "content": "shared keyword",
                "tokenized_content": "shared keyword",
            }],
        )

    save("old-small.txt", 100, 1000.0)
    save("new-large.txt", 5000, 5000.0)
    searcher = DocumentSearcher(db)

    assert [item.filename for item in searcher.search("shared", modified_after=2000)] == [
        "new-large.txt"
    ]
    assert [item.filename for item in searcher.search("shared", max_size=1000)] == [
        "old-small.txt"
    ]
    assert [item.filename for item in searcher.search(
        "filename:new", modified_before=6000, min_size=1000
    )] == ["new-large.txt"]
    assert searcher.search("shared", modified_before=500, max_size=50) == []
    db.close()


def test_advanced_search_modes_paths_and_creation_time(tmp_path):
    db = Database(str(tmp_path / "advanced_filters.db"))

    def save(relative_path, content, mtime, ctime, size):
        db.save_document_index(
            file_path=str(tmp_path / relative_path), file_type="txt",
            file_size=size, mtime=mtime, ctime=ctime,
            segments=[{
                "segment_id": "1", "segment_type": "text", "content": content,
                "tokenized_content": content,
            }],
        )

    save("team/cat.txt", "Cat and feline", 100, 900, 1024)
    save("team/category.txt", "category", 900, 100, 2_000_000)
    save("temp/cat.txt", "cat", 900, 900, 200_000_000)
    searcher = DocumentSearcher(db)

    assert {r.filename for r in searcher.search("cat", whole_word=True)} == {"cat.txt"}
    assert {r.path for r in searcher.search("cat", match_case=True)} == {
        str(tmp_path / "temp/cat.txt")
    }
    assert {r.path for r in searcher.search("cat", include_paths=[str(tmp_path / "team")])} == {
        str(tmp_path / "team/cat.txt")
    }
    assert {r.path for r in searcher.search(
        "cat", exclude_patterns=["temp"], search_roots=[str(tmp_path)]
    )} == {
        str(tmp_path / "team/cat.txt")
    }
    assert {r.path for r in searcher.search("cat", date_field="ctime", modified_after=500)} == {
        str(tmp_path / "team/cat.txt"), str(tmp_path / "temp/cat.txt")
    }
    assert {r.filename for r in searcher.search(r"Cat\s+and", regex=True, match_case=True)} == {
        "cat.txt"
    }
    assert {r.path for r in searcher.search("cat", regex=True, whole_word=True)} == {
        str(tmp_path / "team/cat.txt"), str(tmp_path / "temp/cat.txt")
    }
    assert {r.path for r in searcher.search("NOT category", whole_word=True)} == {
        str(tmp_path / "team/cat.txt"), str(tmp_path / "temp/cat.txt")
    }
    with pytest.raises(SearchQueryError, match="Regex"):
        searcher.search("[", regex=True)
    db.close()


@pytest.mark.parametrize("expression, content", [
    ("report", "annual report"),
    ("cat|dog|bird", "a bird appeared"),
    ("(?i)error", "ERROR found"),
    (r"\d+", "number 2026"),
    (r"\d{4}", "year 2026"),
    (r"\d{4}-\d{2}-\d{2}", "2026-09-23"),
    (r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", "192.168.1.42"),
    ("^IMPORT", "IMPORT records"),
    ("END$", "records END"),
    (r"\btest\b", "a test case"),
    ("LOG.*ERROR", "LOG: ERROR occurred"),
    (r"\b\w+\.(pdf|docx|txt)\b", "see report.pdf"),
])
def test_help_regex_examples_execute_in_search(expression, content, tmp_path):
    db = Database(str(tmp_path / "regex_examples.db"))
    db.save_document_index(
        file_path=str(tmp_path / "example.txt"), file_type="txt",
        file_size=len(content), mtime=100.0, ctime=100.0,
        segments=[{
            "segment_id": "1", "segment_type": "text", "content": content,
            "tokenized_content": content,
        }],
    )
    try:
        assert [result.filename for result in DocumentSearcher(db).search(
            expression, regex=True, match_case=True
        )] == ["example.txt"]
    finally:
        db.close()


def test_include_path_filter_matches_windows_separators(monkeypatch):
    """On Windows os.sep is the LIKE escape character; the subfolder pattern must escape it."""
    import sqlite3

    monkeypatch.setattr(os, "sep", "\\")
    monkeypatch.setattr(os.path, "abspath", lambda path: path)
    clause, params = DocumentSearcher._build_filter_clause(
        "all", None, None, None, None, include_paths=[r"C:\Docs\team"]
    )
    conn = sqlite3.connect(":memory:")

    def included(path):
        sql = "SELECT 1 FROM (SELECT ? AS path) d WHERE 1 " + clause
        return conn.execute(sql, [path, *params]).fetchone() is not None

    assert included(r"C:\Docs\team\cat.txt")
    assert included(r"C:\Docs\team\sub\dog.txt")
    assert not included(r"C:\Docs\teammate\cat.txt")
    assert not included(r"C:\Docs\other\cat.txt")


def test_exclusions_ignore_folders_above_search_roots(tmp_path):
    """A search folder under e.g. %TEMP% or ~/temp must not be hidden by the "temp" rule."""
    root = tmp_path / "Temp" / "temp" / "docs"
    db = Database(str(tmp_path / "exclusions.db"))
    for relative in ("report.txt", "temp/scratch.txt", "private/secret.txt"):
        db.save_document_index(
            file_path=str(root / relative), file_type="txt", file_size=10, mtime=1,
            segments=[{"segment_id": "1", "segment_type": "text",
                       "content": "budget", "tokenized_content": "budget"}],
        )
    searcher = DocumentSearcher(db)

    def found(**filters):
        return {Path(r.path).relative_to(root).as_posix() for r in searcher.search("budget", **filters)}

    roots = [str(root)]
    assert found(exclude_patterns=["temp"], search_roots=roots) == {"report.txt", "private/secret.txt"}
    absolute = (root / "private").as_posix() + "/*"
    assert found(exclude_patterns=[absolute], search_roots=roots) == {"report.txt", "temp/scratch.txt"}
    # Without roots the whole path is matched (legacy callers).
    assert found(exclude_patterns=["temp"]) == set()
    db.close()
