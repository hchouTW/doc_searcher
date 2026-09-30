"""Simplified/Traditional Chinese matching (test plan LANG-06a-c, e, f)."""

import os
import re
import sqlite3

import jieba
import pytest

from doc_searcher.search.script_fold import fold, script_regex, variants
from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.search.text_helper import generate_highlighted_snippets, tokenize_for_fts
from doc_searcher.storage.database import Database

TRADITIONAL = "本次會議記錄：升等審查辦法已通過，計算機 ok 😀 Outstanding"
SIMPLIFIED = "本次会议记录：升等审查办法已通过，计算机 ok 😀 Outstanding"


def add(db, tmp_path, name, content, tokenized=None):
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
                "tokenized_content": tokenize_for_fts(content) if tokenized is None else tokenized,
            }
        ],
    )


@pytest.fixture
def searcher(tmp_path):
    db = Database(str(tmp_path / "index.db"))
    add(db, tmp_path, "trad.txt", TRADITIONAL)
    add(db, tmp_path, "simp.txt", SIMPLIFIED)
    add(db, tmp_path, "other.txt", "nothing relevant here")
    add(db, tmp_path, "會議記錄範本.txt", "template")
    yield DocumentSearcher(db)
    db.close()


def names(results):
    return {r.filename for r in results}


def test_fold_keeps_length_and_leaves_other_text_alone():
    assert fold("會議記錄") == "会议记录"
    assert fold("Outstanding 😀 2023-01-03 A+") == "Outstanding 😀 2023-01-03 A+"
    assert fold("") == ""
    assert len(fold(TRADITIONAL)) == len(TRADITIONAL)
    assert fold(fold(TRADITIONAL)) == fold(TRADITIONAL)


def test_variants_and_regex_cover_both_scripts():
    assert {"发", "發", "髮"} <= variants("发")
    assert variants("發") == variants("发")
    assert variants("a") == frozenset("a")
    assert re.fullmatch(script_regex("会议(1)"), "會議(1)")
    assert not re.fullmatch(script_regex("会议"), "會餓")


@pytest.mark.parametrize("query", ["会议记录", "會議記錄", '"会议记录"', "会议 AND 记录", "計算机"])
def test_query_matches_both_scripts(searcher, query):
    assert names(searcher.search(query)) == {"trad.txt", "simp.txt"}


def test_or_and_not_work_across_scripts(searcher):
    assert names(searcher.search("升等 NOT 会议")) == set()
    assert names(searcher.search("会议记录 OR template")) == {
        "trad.txt",
        "simp.txt",
        "會議記錄範本.txt",
    }
    assert names(searcher.search("NOT 会议")) == {"other.txt", "會議記錄範本.txt"}


def test_filename_search_matches_across_scripts(searcher):
    assert names(searcher.search("filename:会议记录")) == {"會議記錄範本.txt"}
    assert names(searcher.search("檔名:會議記錄")) == {"會議記錄範本.txt"}


@pytest.mark.parametrize("match_case,whole_word", [(True, False), (False, True), (True, True)])
def test_case_and_whole_word_options_keep_cross_script_matching(
    searcher, tmp_path, match_case, whole_word
):
    add(searcher.db, tmp_path, "w_trad.txt", "x 會議記錄 x")  # CJK is \w, so whole word needs gaps
    add(searcher.db, tmp_path, "w_simp.txt", "x 会议记录 x")
    expected = {"w_trad.txt", "w_simp.txt"}
    for query in ("会议记录", "會議記錄"):
        found = names(searcher.search(query, match_case=match_case, whole_word=whole_word))
        assert expected <= found
        if whole_word:
            assert found == expected
    assert names(searcher.search("outstanding", match_case=True)) == set()
    assert names(searcher.search("Outstanding", match_case=True)) == {"trad.txt", "simp.txt"}


def test_like_fallback_matches_across_scripts(searcher):
    rows = searcher._fallback_like_search(["会议记录"], "", [], 10)
    assert {os.path.basename(r["path"]) for r in rows} == {"trad.txt", "simp.txt"}


@pytest.mark.parametrize("query", ["会议记录", "會議記錄"])
def test_highlight_shows_document_characters(searcher, query):
    for result in searcher.search(query):
        marked = re.findall(r">([^<]+)</mark>", " ".join(result.segments[0].snippets))
        original = "會議記錄" if result.filename == "trad.txt" else "会议记录"
        assert marked and all(m == original for m in marked if len(m) == 4)
        assert original in marked


def test_highlight_offsets_hold_with_emoji_and_english():
    content = "😀 hello 會議 world 😀 会议"
    snippets = generate_highlighted_snippets(content, ["会议"])
    assert re.findall(r">([^<]+)</mark>", snippets[0]) == ["會議", "会议"]


def test_one_to_many_characters_match_without_error(searcher, tmp_path):
    add(searcher.db, tmp_path, "cadre.txt", "幹部會議")
    add(searcher.db, tmp_path, "clean.txt", "乾淨整潔")
    add(searcher.db, tmp_path, "hair.txt", "他的髮型")
    add(searcher.db, tmp_path, "grow.txt", "公司發展")
    assert names(searcher.search("干部")) == {
        "cadre.txt"
    }  # 干 covers 幹 and 乾, words still differ
    assert names(searcher.search("干净")) == {"clean.txt"}
    assert names(searcher.search("发型")) == {"hair.txt"}
    assert names(searcher.search("发展")) == {"grow.txt"}
    assert names(searcher.search("filename:干")) == set()  # no such file names, and no crash


def test_english_and_same_script_results_unchanged(searcher):
    assert names(searcher.search("outstanding")) == {"trad.txt", "simp.txt"}
    assert names(searcher.search("升等")) == {"trad.txt", "simp.txt"}
    assert names(searcher.search("nonexistentterm")) == set()


def test_regex_mode_stays_literal(searcher):
    assert names(searcher.search("会议", regex=True)) == {"simp.txt"}


def test_migration_refolds_existing_index_without_source_files(tmp_path):
    path = tmp_path / "index.db"
    db = Database(str(path))
    old_tokens = " ".join(jieba.cut_for_search(TRADITIONAL))  # what v1.3.0 stored: unfolded
    add(db, tmp_path, "gone/trad.txt", TRADITIONAL, tokenized=old_tokens)
    db.close()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA user_version = 1")
    conn.close()
    assert not (tmp_path / "gone").exists()

    reopened = Database(str(path))
    assert reopened.applied_migrations == [2]
    assert names(DocumentSearcher(reopened).search("会议记录")) == {"trad.txt"}
    counts = (
        reopened.get_connection()
        .execute("SELECT (SELECT COUNT(*) FROM doc_fts), (SELECT COUNT(*) FROM doc_segments)")
        .fetchone()
    )
    assert counts[0] == counts[1] == 1
    reopened.close()

    again = Database(str(path))
    assert again.applied_migrations == []
    again.close()
