"""Occurrence counting and original-text interval regressions."""

import pytest
from doc_searcher.storage.database import Database
from doc_searcher.search.searcher import DocumentSearcher, SearchQueryError
from doc_searcher.search.text_helper import tokenize_for_fts, generate_highlighted_snippets


def indexed(tmp_path, text):
    db = Database(str(tmp_path / "index.db"))
    db.save_document_index(
        str(tmp_path / "a.xlsx"),
        "xlsx",
        10,
        1,
        [
            {
                "segment_id": "Sheet1",
                "segment_type": "sheet",
                "content": text,
                "tokenized_content": tokenize_for_fts(text),
            }
        ],
    )
    return db, DocumentSearcher(db)


def test_counts_all_eight_not_snippets(tmp_path):
    db, searcher = indexed(tmp_path, "會議 " * 8)
    result = searcher.search("會議")[0]
    assert result.total_matches == 8
    assert result.segment_count == 1
    assert result.snippet_count == 1
    db.close()


def test_snippets_skip_close_matches_and_continue(tmp_path):
    text = "會議會議會議" + ("x" * 160 + "會議") * 7
    db, searcher = indexed(tmp_path, text)
    assert searcher.search("會議")[0].total_matches == 10
    assert len(generate_highlighted_snippets(text, ["會議"], max_snippets=3)) == 3
    db.close()


@pytest.mark.parametrize(
    "text,query,count",
    [
        ("會議 會議秘密", "會議 OR 會議", 2),
        ("會議秘密", '"會議秘密"', 1),
        ("會議公開 秘密", "會議 NOT 隱藏", 1),
        ("會議公開", "會議 OR 議公", 1),
        ("會議會議", "會議", 2),
        ("甲甲甲甲甲", "甲甲甲", 1),
    ],
)
def test_positive_occurrence_contract(tmp_path, text, query, count):
    db, searcher = indexed(tmp_path, text)
    assert searcher.search(query)[0].total_matches == count
    db.close()


def test_original_offsets_merge_and_adjacency():
    from doc_searcher.search.matches import iter_locations

    locations = list(iter_locations("😀都市計畫計畫", ["都市計畫", "計畫", "计画"]))
    assert [(x.start, x.end) for x in locations] == [(1, 5), (5, 7)]


def test_zero_width_regex_count_and_cancellation(tmp_path):
    db, searcher = indexed(tmp_path, "aaa")
    assert searcher.search("(?=a)", regex=True)[0].total_matches == 3
    with pytest.raises(SearchQueryError) as exc:
        searcher.search("a", regex=True, cancel_check=lambda: True)
    assert exc.value.code == "cancelled"
    db.close()


def test_stemmed_phrase_is_one_complete_original_interval(tmp_path):
    db, searcher = indexed(tmp_path, "The quick brown foxes jumped.")
    page = searcher.search_page('"quick brown fox"')
    locations = searcher.match_locations(
        '"quick brown fox"', page.items[0].doc_id, revision=page.revision
    )
    assert [(x.start, x.end) for x in locations.locations] == [(4, 21)]
    db.close()


def test_phrase_crossing_source_spans_reports_both_cells(tmp_path):
    db = Database(str(tmp_path / "index.db"))
    text = "annual report"
    db.save_document_index(
        str(tmp_path / "a.xlsx"),
        "xlsx",
        10,
        1,
        [
            {
                "segment_id": "Sheet1",
                "segment_type": "sheet",
                "content": text,
                "tokenized_content": tokenize_for_fts(text),
                "sources": [
                    {"start": 0, "end": 6, "source": {"kind": "cell", "location": "Sheet1!A1"}},
                    {"start": 7, "end": 13, "source": {"kind": "cell", "location": "Sheet1!B1"}},
                ],
            }
        ],
    )
    searcher = DocumentSearcher(db)
    result = searcher.search('"annual report"')[0]
    location = searcher.match_locations('"annual report"', result.doc_id).locations[0]
    assert location.source["kind"] == "range"
    assert [source["location"] for source in location.source["locations"]] == [
        "Sheet1!A1",
        "Sheet1!B1",
    ]
    db.close()
