"""DEF-02: a Chinese term jieba cannot segment must match as the word, not as scattered characters."""

import pytest

from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.search.text_helper import tokenize_for_fts
from doc_searcher.storage.database import Database

DOCS = {
    "word.txt": "教師升等審查辦法",
    "apart.txt": "升 and 等 appear apart: 上升 等於 提升 平等",
    "reversed.txt": "等待升起",
    "trad_meeting.txt": "會議與記錄分開，會議先開，記錄後補",
    "simp_word.txt": "教师升等申请",
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


@pytest.mark.parametrize("query", ["升等", '"升等"', "升等審查", "升等 OR 不存在的詞"])
def test_unsegmented_term_matches_only_documents_containing_it(searcher, query):
    expected = {"word.txt", "simp_word.txt"} if query != "升等審查" else {"word.txt"}
    assert found(searcher, query) == expected


def test_scattered_characters_and_reversed_order_do_not_match(searcher):
    hits = found(searcher, "升等")
    assert "apart.txt" not in hits and "reversed.txt" not in hits
    assert found(searcher, "等升") == set()


def test_negation_uses_the_word_too(searcher):
    assert found(searcher, "教師 NOT 升等") == set()
    assert "apart.txt" in found(searcher, "appear NOT 升等")


def test_segmentable_words_keep_and_semantics(searcher):
    # 會議 and 記錄 are known words: they are ANDed, so they may be apart, and order is free.
    assert found(searcher, "會議 記錄") == {"trad_meeting.txt"}
    assert found(searcher, "記錄會議") == set()


def test_match_case_and_whole_word_still_work_with_unsegmented_terms(searcher):
    assert found(searcher, "升等", match_case=True) == {"word.txt", "simp_word.txt"}
