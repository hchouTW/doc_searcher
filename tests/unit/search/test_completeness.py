"""Literal recall, boolean scope and stable document continuation regressions."""
import pytest

from doc_searcher.search.searcher import DocumentSearcher, SearchQueryError
from doc_searcher.search.text_helper import tokenize_for_fts
from doc_searcher.storage.database import Database


@pytest.fixture
def corpus(tmp_path):
    db = Database(str(tmp_path / "index.db"))
    def save(name, texts):
        return db.save_document_index(str(tmp_path / name), "txt", 100, 1,
            [{"segment_id": str(i), "segment_type": "section", "content": text,
              "tokenized_content": tokenize_for_fts(text)} for i, text in enumerate(texts)])
    yield db, DocumentSearcher(db), save
    db.close()


@pytest.mark.parametrize("query", ["計畫", "计画", "計", '"計畫"'])
def test_compound_recall(corpus, query):
    _, searcher, save = corpus
    save("trad.txt", ["都市計畫"])
    save("simp.txt", ["都市计画"])
    assert {x.filename for x in searcher.search(query)} == {"trad.txt", "simp.txt"}


def test_literal_and_boolean_scope(corpus):
    _, searcher, save = corpus
    save("compound.txt", ["教師升等級審查"])
    save("apart.txt", ["教師升級等待審查"])
    assert [x.filename for x in searcher.search("升等")] == ["compound.txt"]
    save("branch.txt", ["甲 丙"])
    save("excluded.txt", ["乙 丙"])
    save("second.txt", ["乙"])
    assert {x.filename for x in searcher.search("甲 OR 乙 NOT 丙")} == {"branch.txt", "second.txt"}
    save("pages.txt", ["丁", "戊"])
    assert not searcher.search("丁 戊")


def test_rank_documents_before_limiting_segments(corpus):
    _, searcher, save = corpus
    save("many.txt", ["budget"] * 7)
    save("other.txt", ["budget " + "extra " * 100])
    results = searcher.search("budget", limit=2)
    assert {x.filename for x in results} == {"many.txt", "other.txt"}
    assert len(next(x for x in results if x.filename == "many.txt").segments) == 7


def test_continuation_and_stale_revision(corpus):
    db, searcher, save = corpus
    for i in range(5):
        save(f"{i}.txt", ["計畫"])
    first = searcher.search_page("計畫", limit=2)
    assert first.has_more and first.next_cursor and first.complete
    seen = [x.doc_id for x in first.items]
    cursor = first.next_cursor
    while cursor:
        page = searcher.search_page("計畫", limit=2, cursor=cursor)
        seen.extend(x.doc_id for x in page.items)
        cursor = page.next_cursor
    assert len(seen) == len(set(seen)) == 5
    db.delete_document(first.items[0].path)
    with pytest.raises(SearchQueryError) as error:
        searcher.search_page("計畫", limit=2, cursor=first.next_cursor)
    assert error.value.code == "stale_cursor"

@pytest.mark.parametrize("query,oracle", [
    ("計畫", lambda t: "计画" in t),
    ('"都市計畫"', lambda t: "都市计画" in t),
    ("計 畫", lambda t: "计" in t and "画" in t),
    ("計畫 OR 升等", lambda t: "计画" in t or "升等" in t),
    ("計畫 NOT 都市", lambda t: "计画" in t and "都市" not in t),
    ("計畫 OR 教師 NOT 升等", lambda t: "计画" in t or ("教师" in t and "升等" not in t)),
])
def test_index_matches_folded_oracle(corpus, query, oracle):
    from doc_searcher.search.script_fold import fold
    _, searcher, save = corpus
    texts = ["都市計畫", "計 畫", "教師升等級審查", "教師公告", "计画", "𠀀计画"]
    for i, text in enumerate(texts):
        save(f"oracle{i}.txt", [text])
    expected = {f"oracle{i}.txt" for i, t in enumerate(texts) if oracle(fold(t))}
    assert {x.filename for x in searcher.search(query)} == expected


def test_cjk_migration_rollback_and_rebuild_without_files(corpus, monkeypatch):
    import sqlite3
    from doc_searcher.storage import migrations
    from doc_searcher.storage.errors import MigrationError
    db, _, save = corpus
    save("missing.txt", ["都市計畫𠀀"])
    path = db.db_path
    db.close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE doc_cjk_fts")
    conn.execute("DROP TABLE index_state")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    original = migrations._v4_cjk
    def broken(conn):
        original(conn)
        raise RuntimeError("injected index build failure")
    monkeypatch.setattr(migrations, "MIGRATIONS", [(*m[:2], broken) if m[0] == 4 else m for m in migrations.MIGRATIONS])
    with pytest.raises(MigrationError):
        Database(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='doc_cjk_fts'").fetchone()
    conn.close()
    monkeypatch.undo()
    rebuilt = Database(path)
    assert DocumentSearcher(rebuilt).search("計畫")
    assert DocumentSearcher(rebuilt).search("𠀀")
    rebuilt.close()
