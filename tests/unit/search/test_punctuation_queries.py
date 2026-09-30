"""Queries containing punctuation (DEF-01): -, _, (, ), /, +, % must find their text."""

import re

import pytest

from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.search.text_helper import (
    extract_keywords_from_query,
    generate_highlighted_snippets,
    has_word_char,
    tokenize_for_fts,
)
from doc_searcher.storage.database import Database

DOCS = {
    "ids.txt": "Ticket QATREE-deep opened on 2023-01-03 by snake_case owner.",
    "dash_only_words.txt": "QATREE and deep appear here but never joined, nor 2023 01 03.",
    "grades.txt": "Grades A- and B+ were awarded.",
    "plus.txt": "Grade A+ for everyone. Language C++ too.",
    "section.txt": "Section 1 (Paragraphs) follows Section 2 (Tables).",
    "path.txt": "Copy from a/b to c/d. Progress 100% done.",
    "plain.txt": "budget and forecast for 升等 and 會議記錄",
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


@pytest.mark.parametrize(
    "query,expected",
    [
        ("QATREE-deep", {"ids.txt"}),
        ("2023-01-03", {"ids.txt"}),
        ("snake_case", {"ids.txt"}),
        ("A-", {"grades.txt"}),
        ("A+", {"plus.txt"}),
        ("C++", {"plus.txt"}),
        ("100%", {"path.txt"}),
        ("a/b", {"path.txt"}),
        ("Section 1 (Paragraphs)", {"section.txt"}),
        ("(Tables)", {"section.txt"}),
        ('"Section 1 (Paragraphs)"', {"section.txt"}),
    ],
)
def test_punctuation_queries_find_only_the_literal_text(searcher, query, expected):
    assert found(searcher, query) == expected


@pytest.mark.parametrize(
    "query,expected", [("_", {"ids.txt"}), ("/", {"path.txt"}), ("%", {"path.txt"})]
)
def test_punctuation_only_queries_scan_the_stored_text(searcher, query, expected):
    assert found(searcher, query) == expected


def test_punctuation_combines_with_other_terms(searcher):
    assert found(searcher, "Grades A-") == {"grades.txt"}
    assert found(searcher, "A- OR A+") == {"grades.txt", "plus.txt"}
    # "Grade" also finds "Grades" (stemming), so grades.txt qualifies; plus.txt has A+.
    assert found(searcher, "Grade NOT A+") == {"grades.txt"}
    assert found(searcher, "QATREE-deep AND 2023-01-03") == {"ids.txt"}
    assert found(searcher, "Language 100%") == set()  # no single segment holds both


def test_options_still_apply_to_punctuation_queries(searcher):
    assert found(searcher, "qatree-deep", match_case=True) == set()
    assert found(searcher, "QATREE-deep", match_case=True) == {"ids.txt"}
    assert found(searcher, "qatree-deep") == {"ids.txt"}


def test_queries_without_punctuation_are_unaffected(searcher):
    assert found(searcher, "budget") == {"plain.txt"}
    assert found(searcher, "升等 會議記錄") == {"plain.txt"}
    assert found(searcher, "QATREE") == {"ids.txt", "dash_only_words.txt"}


def test_highlights_cover_the_whole_term_not_every_letter():
    keywords = extract_keywords_from_query("A-")
    assert keywords == ["A-"]
    snippet = generate_highlighted_snippets("Grade A- and a banana", keywords)[0]
    assert re.findall(r">([^<]+)</mark>", snippet) == ["A-"]
    keywords = extract_keywords_from_query("Section 1 (Paragraphs)")
    assert "(" not in keywords and ")" not in keywords
    marked = re.findall(
        r">([^<]+)</mark>", generate_highlighted_snippets("x (Paragraphs) y (z)", keywords)[0]
    )
    assert marked == ["(Paragraphs)"]


def test_has_word_char():
    assert has_word_char("a1") and has_word_char("會") and has_word_char("(x)")
    assert not has_word_char("-") and not has_word_char("_") and not has_word_char("（）")
