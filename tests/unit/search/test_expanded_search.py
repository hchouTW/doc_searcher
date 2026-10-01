"""Offsets, expanded-query behavior, and stale semantic snapshots."""

import pytest
from doc_searcher.storage.database import Database
from doc_searcher.indexing.service import IndexRequest, IndexingService
from doc_searcher.search.searcher import DocumentSearcher


def test_chunks_preserve_offsets_overlap_and_tail():
    from doc_searcher.search.chunks import chunk_text

    text = "第一段。\n\n" + "會議資料" * 500 + "\n\nfinal sentence."
    chunks = chunk_text(text)
    assert len(chunks) > 2
    assert chunks[0].start == 0 and chunks[-1].end == len(text)
    for chunk in chunks:
        assert chunk.text == text[chunk.start : chunk.end]
        assert len(chunk.text) <= 1000
    assert all(a.end > b.start and b.end > a.end for a, b in zip(chunks, chunks[1:], strict=False))
    covered = set().union(*(set(range(c.start, c.end)) for c in chunks))
    assert len(covered) == len(text)


def indexed(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "literal.txt").write_text("會議 exact PROJECT-7429")
    (root / "synonym.txt").write_text("研討會 domain seminar")
    (root / "typo.txt").write_text("coverage analysis")
    db = Database(str(tmp_path / "index.db"))
    IndexingService(db).run(IndexRequest([str(root)]))
    return db


def test_synonyms_and_typos_preserve_exact_hits_and_literal_counts(tmp_path):
    db = indexed(tmp_path)
    searcher = DocumentSearcher(db)
    page = searcher.search_page("會議", search_mode="expanded", synonyms={"會議": ["研討會"]})
    assert {r.filename for r in page.items} == {"literal.txt", "synonym.txt"}
    literal = next(r for r in page.items if r.filename == "literal.txt")
    expanded = next(r for r in page.items if r.filename == "synonym.txt")
    assert literal.total_matches == 1
    assert expanded.total_matches == 0 and expanded.passages
    assert expanded.matched_by == ["synonym"]
    assert not DocumentSearcher(db).search("coverge")
    fuzzy = searcher.search_page("coverge", search_mode="expanded").items
    assert [r.filename for r in fuzzy] == ["typo.txt"]
    assert fuzzy[0].total_matches == 0
    assert not searcher.search_page("PROJECT-7439", search_mode="expanded").items
    db.close()


def test_expanded_pagination_and_filters(tmp_path):
    db = indexed(tmp_path)
    searcher = DocumentSearcher(db)
    options = dict(search_mode="expanded", synonyms={"會議": ["研討會"]})
    first = searcher.search_page("會議", limit=1, **options)
    second = searcher.search_page("會議", limit=1, cursor=first.next_cursor, **options)
    assert first.has_more and not second.has_more
    assert first.items[0].doc_id != second.items[0].doc_id
    restricted = searcher.search_page(
        "會議", include_paths=[str(tmp_path / "docs" / "literal.txt")], **options
    )
    assert [r.filename for r in restricted.items] == ["literal.txt"]
    db.close()


def test_hybrid_missing_model_is_actionable(tmp_path, monkeypatch):
    monkeypatch.delenv("DOC_SEARCHER_EMBEDDING_MODEL", raising=False)
    db = indexed(tmp_path)
    with pytest.raises(ValueError, match="model"):
        DocumentSearcher(db).search_page("concept", search_mode="hybrid")
    assert DocumentSearcher(db).search("會議")
    db.close()


def test_semantic_storage_rejects_changed_source(tmp_path):
    from doc_searcher.search.semantic import SemanticIndex

    db = indexed(tmp_path)

    class Encoder:
        model_id = "test-dense-provider"

        def encode(self, texts, query=False):
            # A controlled dense-provider boundary makes revision races deterministic.
            db.delete_document(str(tmp_path / "docs" / "literal.txt"))
            return [[1.0, 0.0] for _ in texts]

        def token_count(self, text):
            return len(text)

        max_tokens = 512

    index = SemanticIndex(db, Encoder())
    with pytest.raises(ValueError, match="changed"):
        index.rebuild()
    assert db.get_connection().execute("SELECT COUNT(*) FROM semantic_chunks").fetchone()[0] == 0
    db.close()


def test_real_multilingual_dense_retrieval(tmp_path):
    import os

    if not os.environ.get("DOC_SEARCHER_EMBEDDING_MODEL"):
        pytest.skip("Local multilingual embedding model not configured")
    root = tmp_path / "docs"
    root.mkdir()
    (root / "hr.txt").write_text("員工休假規定：每位同仁每年可申請十五天帶薪假期。")
    (root / "finance.txt").write_text("年度財務報表記錄營業收入、支出及資產負債。")
    (root / "engineering.txt").write_text(
        "The database server uses replication for high availability."
    )
    db = Database(str(tmp_path / "index.db"))
    IndexingService(db).run(IndexRequest([str(root)]))
    page = DocumentSearcher(db).search_page("annual paid leave policy", search_mode="hybrid")
    assert page.semantic_ready
    assert page.items[0].filename == "hr.txt"
    assert page.items[0].total_matches == 0 and "semantic" in page.items[0].matched_by
    assert "帶薪假期" in page.items[0].passages[0]["text"]
    chinese = DocumentSearcher(db).search_page("放假辦法", search_mode="hybrid")
    assert chinese.items[0].filename == "hr.txt"
    db.close()


@pytest.mark.parametrize("query", ["7", "74", "PROJECT-7429", "會", "會議"])
def test_short_queries_and_identifiers_have_no_tokenizer_omissions(tmp_path, query):
    db = indexed(tmp_path)
    results = DocumentSearcher(db).search(query)
    assert {r.filename for r in results} == (
        {"literal.txt", "synonym.txt"} if query == "會" else {"literal.txt"}
    )
    db.close()


def test_stemmed_synonym_passage_contains_actual_match(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "policy.txt").write_text("unrelated " * 100 + " company policies cover leave.")
    db = Database(str(tmp_path / "index.db"))
    IndexingService(db).run(IndexRequest([str(root)]))
    item = (
        DocumentSearcher(db)
        .search_page("regulation", search_mode="expanded", synonyms={"regulation": ["policy"]})
        .items[0]
    )
    assert "policies" in item.passages[0]["text"]
    assert item.passages[0]["start"] > 800
    db.close()
