"""English stemming: word forms match each other in search, highlights and after migration."""

import re
import sqlite3

import pytest

from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.search.stemming import matches_stem, stem, stem_spec
from doc_searcher.search.text_helper import generate_highlighted_snippets, tokenize_for_fts
from doc_searcher.storage.database import Database

DOCS = {
    "single.txt": "One outstanding result was reported.",
    "plural.txt": "Several outstandings remain open.",
    "verb.txt": "They outstand every competitor.",
    "running.txt": "The team is running daily tests.",
    "runway.txt": "The runway was closed.",
    "news.txt": "Read the news today.",
    "new.txt": "A new release is out.",
    "zh.txt": "會議記錄與升等 outstanding",
    "quoted.txt": "The quick brown foxes jumped.",
}


@pytest.fixture
def searcher(tmp_path):
    db = Database(str(tmp_path / "index.db"))
    for name, content in DOCS.items():
        db.save_document_index(
            file_path=str(tmp_path / name),
            file_type="txt",
            file_size=len(content),
            mtime=1.0,
            segments=[
                {
                    "segment_id": "1",
                    "segment_type": "text",
                    "content": content,
                    "tokenized_content": tokenize_for_fts(content),
                }
            ],
        )
    yield DocumentSearcher(db)
    db.close()


def found(searcher, query, **options):
    return {r.filename for r in searcher.search(query, **options)}


def marks(item):
    return re.findall(r">([^<]+)</mark>", " ".join(s for g in item.segments for s in g.snippets))


def test_stem_uses_the_same_porter_stems_as_the_index():
    assert stem("outstanding") == stem("outstandings") == stem("outstand") == "outstand"
    assert stem("Running") == "run"
    assert stem("happy") == "happi"


def test_stem_spec_only_covers_plain_english_words():
    assert stem_spec("outstanding").prefix == "outstand"
    assert stem_spec("happy").prefix == "happ"  # the stem "happi" is not a prefix of "happy"
    for term in ("of", "升等", "abc123", "A-", "Section 1"):
        assert stem_spec(term) is None
    assert matches_stem(stem_spec("run"), "Running") and not matches_stem(
        stem_spec("run"), "runway"
    )


@pytest.mark.parametrize("query", ["outstand", "outstanding", "outstandings", "Outstanding"])
def test_every_form_finds_every_form(searcher, query):
    assert found(searcher, query) == {"single.txt", "plural.txt", "verb.txt", "zh.txt"}


def test_stemming_works_in_phrases_and_boolean_queries(searcher):
    assert found(searcher, '"quick brown fox"') == {"quoted.txt"}  # foxes -> fox
    assert found(searcher, "run AND daily") == {"running.txt"}
    assert found(searcher, "outstanding NOT plural") == {
        "single.txt",
        "plural.txt",
        "verb.txt",
        "zh.txt",
    }
    assert found(searcher, "NOT outstanding") >= {"running.txt", "runway.txt"}
    assert not found(searcher, "NOT outstanding") & {"single.txt", "plural.txt", "verb.txt"}


def test_porter_over_stemming_is_accepted_and_visible(searcher):
    # Porter maps news -> new, exactly as the index does; recorded so a change is noticed.
    assert found(searcher, "news") == {"news.txt", "new.txt"}
    assert found(searcher, "run") == {"running.txt"}  # but run does not reach runway


def test_highlights_mark_the_other_forms_but_only_real_ones(searcher):
    (plural,) = [r for r in searcher.search("outstanding") if r.filename == "plural.txt"]
    assert marks(plural) == ["outstandings"]
    (running,) = searcher.search("run")
    assert marks(running) == ["running"]
    assert "runway" not in marks(running)


def test_highlight_helper_handles_mixed_keywords():
    snippet = generate_highlighted_snippets(
        "Several outstandings and running runway 升等", ["outstanding", "run", "升等"]
    )[0]
    assert re.findall(r">([^<]+)</mark>", snippet) == ["outstandings", "running", "升等"]


def test_match_case_and_whole_word_ask_for_the_exact_word(searcher):
    assert found(searcher, "outstanding", whole_word=True) == {"single.txt", "zh.txt"}
    assert found(searcher, "outstandings", whole_word=True) == {"plural.txt"}
    # match case is a substring check: outstandings contains outstanding, outstand does not
    assert found(searcher, "outstanding", match_case=True) == {"single.txt", "plural.txt", "zh.txt"}
    assert found(searcher, "OUTSTANDING", match_case=True) == set()


def test_chinese_and_filename_search_are_unaffected(searcher):
    assert found(searcher, "會議記錄") == {"zh.txt"}
    assert found(searcher, "升等") == {"zh.txt"}
    assert found(searcher, "filename:single") == {"single.txt"}
    assert found(searcher, "filename:runs") == set()  # file names are matched literally


def test_migration_from_a_pre_stemming_index_keeps_working(tmp_path):
    path = tmp_path / "index.db"
    db = Database(str(path))
    content = "Several outstandings remain open."
    db.save_document_index(
        file_path=str(tmp_path / "plural.txt"),
        file_type="txt",
        file_size=len(content),
        mtime=1.0,
        segments=[
            {
                "segment_id": "1",
                "segment_type": "text",
                "content": content,
                "tokenized_content": tokenize_for_fts(content),
            }
        ],
    )
    db.close()
    conn = sqlite3.connect(str(path))
    conn.execute("DROP TABLE doc_fts")  # back to the version 2 table: plain unicode61
    conn.execute(
        "CREATE VIRTUAL TABLE doc_fts USING fts5(doc_id UNINDEXED, segment_id UNINDEXED, "
        "segment_type UNINDEXED, content, tokenized_content, tokenize='unicode61')"
    )
    conn.execute(
        "INSERT INTO doc_fts VALUES ('1', '1', 'text', ?, ?)", (content, tokenize_for_fts(content))
    )
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    reopened = Database(str(path))
    assert reopened.applied_migrations == [3, 4, 5]
    assert {r.filename for r in DocumentSearcher(reopened).search("outstanding")} == {"plural.txt"}
    sql = (
        reopened.get_connection()
        .execute("SELECT sql FROM sqlite_master WHERE name = 'doc_fts'")
        .fetchone()[0]
    )
    assert "porter" in sql
    reopened.close()
