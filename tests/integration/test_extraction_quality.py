"""Persist quality/sources and explicitly reprocess unchanged selected files."""
import os
import pytest

from doc_searcher.storage.database import Database
from doc_searcher.indexing.service import IndexingService, IndexRequest
from doc_searcher.search.searcher import DocumentSearcher


def test_quality_and_force_unchanged_file(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    path, empty = root / "a.txt", root / "blank.txt"
    path.write_text("before")
    empty.write_text("")
    db = Database(str(tmp_path / "index.db"))
    service = IndexingService(db)
    service.run(IndexRequest([str(root)]))
    assert db.get_document_by_path(str(empty))["parse_status"] == "empty"
    old_stat = path.stat()
    path.write_text("after!")
    os.utime(path, (old_stat.st_atime, old_stat.st_mtime))
    assert service.run(IndexRequest([str(root)]))["indexed"] == 0
    assert service.run(IndexRequest([str(root)], force_paths=[str(path)]))["indexed"] == 1
    assert DocumentSearcher(db).search("after")
    stats = db.get_stats()
    assert stats["discovered_documents"] == 2 and stats["searchable_documents"] == 1
    assert stats["quality_counts"]["empty"] == 1
    assert db.problem_documents()["documents"][0]["parse_status"] == "empty"
    db.close()


def test_partial_sources_survive_reopen_and_stale_locations(tmp_path):
    import openpyxl
    root = tmp_path / "docs"
    root.mkdir()
    path = root / "formula.xlsx"
    wb = openpyxl.Workbook()
    for i in range(1, 9):
        wb.active.cell(i, 2, "會議")
    wb.active["C1"] = '="missing-cache"'
    wb.save(path)
    dbpath = str(tmp_path / "index.db")
    db = Database(dbpath)
    IndexingService(db).run(IndexRequest([str(root)]))
    doc = db.get_document_by_path(str(path))
    assert doc["parse_status"] == "partial" and doc["warnings"]
    assert doc["parser_version"] == "2"
    db.close()
    db = Database(dbpath)
    searcher = DocumentSearcher(db)
    page = searcher.search_page("會議")
    locations = searcher.match_locations("會議", page.items[0].doc_id, revision=page.revision)
    assert len(locations.locations) == 8
    assert [x.source["location"] for x in locations.locations] == [f"Sheet!B{i}" for i in range(1, 9)]
    IndexingService(db).run(IndexRequest([str(root)], force_paths=[str(path)]))
    with pytest.raises(ValueError):
        searcher.match_context(locations.locations[0])
    db.close()


def test_force_paths_respect_scope_and_exclusions(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    a, b, outside = root / "a.txt", root / "b.txt", tmp_path / "outside.txt"
    for p in (a, b, outside):
        p.write_text("meeting")
    db = Database(str(tmp_path / "index.db"))
    service = IndexingService(db)
    service.run(IndexRequest([str(root)]))
    stats = service.run(IndexRequest([str(root)], exclude_patterns=["b.txt"],
                                      force_paths=[str(a), str(b), str(outside)]))
    assert stats["indexed"] == 1
    assert db.get_document_by_path(str(outside)) is None
    db.close()


def test_legacy_quality_unknown_and_migration_rollback(tmp_path, monkeypatch):
    import sqlite3
    from doc_searcher.storage import migrations
    from doc_searcher.storage.errors import MigrationError
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    migrations._v1_baseline(conn)
    migrations._v3_stemming(conn)
    migrations._v4_cjk(conn)
    conn.execute("INSERT INTO documents(path,filename,file_type,file_size,mtime,indexed_at) VALUES ('missing.txt','missing.txt','txt',1,1,1)")
    conn.execute("PRAGMA user_version=4")
    conn.commit()
    conn.close()
    original = migrations._v5_quality
    def broken(conn):
        original(conn)
        raise RuntimeError("injected quality migration failure")
    monkeypatch.setattr(migrations, "MIGRATIONS", [(*m[:2], broken) if m[0] == 5 else m for m in migrations.MIGRATIONS])
    with pytest.raises(MigrationError):
        Database(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert "parse_status" not in {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
    conn.close()
    monkeypatch.undo()
    db = Database(path)
    assert db.problem_documents()["documents"][0]["parse_status"] == "unknown"
    assert db.problem_documents()["documents"][0]["reprocess_eligible"]
    db.close()


def test_selected_reprocess_only_leaves_other_changed_files_alone(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    a, b = root / "a.txt", root / "b.txt"
    for p in (a, b):
        p.write_text("old")
    db = Database(str(tmp_path / "index.db"))
    service = IndexingService(db)
    service.run(IndexRequest([str(root)]))
    a.write_text("selected")
    b.write_text("unselected")
    stats = service.run(IndexRequest([str(root)], force_paths=[str(a)], reprocess_only=True))
    assert stats["indexed"] == 1
    assert not DocumentSearcher(db).search("unselected")
    db.close()
