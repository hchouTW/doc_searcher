"""Language and query cases from docs/test-plan.md, run against the generated dataset."""

import re
import time

import pytest

from doc_searcher.search.searcher import SearchQueryError

SYMBOLS = "mix/symbols.txt"


@pytest.mark.parametrize("query", ["1111223pi retreat", '"1111223pi retreat"'])
def test_lang_08_mixed_alphanumeric_hits_body_and_file_name(dataset_search, query):
    assert dataset_search(query) == {"mix/1111223pi retreat.txt"}
    assert dataset_search("filename:1111223pi") == {"mix/1111223pi retreat.txt"}


@pytest.mark.parametrize("query", ["Section 1 (Paragraphs)", '"Section 1 (Paragraphs)"'])
def test_lang_09_parentheses_are_not_a_syntax_error(dataset_search, query):
    assert dataset_search(query) == {"en/section.txt"}


@pytest.mark.parametrize("query", ["2023-01-03", "A-", "A+", "snake_case", "a/b", "C++", "100%"])
def test_lang_10_special_characters_find_their_text(dataset_search, query):
    assert SYMBOLS in dataset_search(query)


def test_lang_10_a_minus_is_not_a_plus(dataset_search):
    # Each grade appears once in symbols.txt, so the two queries must not be interchangeable
    # anywhere else in the dataset.
    assert dataset_search("A-") == {SYMBOLS}
    assert dataset_search("A+") == {SYMBOLS}


@pytest.mark.parametrize("query", ["_", "/"])
def test_lang_10_punctuation_only_queries_answer_instead_of_failing(dataset_search, query):
    assert SYMBOLS in dataset_search(query)


def test_marker_style_queries_with_hyphens_work_too(dataset_search):
    assert dataset_search("QATREEdeep") == {"tree/sub/deep/c.txt"}
    assert dataset_search("QATREE-deep") == set()  # the hyphenated form is not in the dataset


# ---- LANG-01 .. LANG-07, LANG-11 .. LANG-14 -------------------------------------------------
TRAD_SIMP_KEYWORD_DOCS = {
    "zh/trad_meeting.txt",
    "zh/simp_meeting.txt",
    "zh/trad_report.docx",
    "zh/simp_report.docx",
    SYMBOLS,
}


def test_lang_01_quoted_chinese_finds_exactly_the_documents_with_the_word(env):
    assert env.search('"升等"') == TRAD_SIMP_KEYWORD_DOCS
    assert env.search('"升等"') <= env.search("升等")


def test_lang_01_unquoted_chinese_matches_the_word_not_its_scattered_characters(env):
    # DEF-02: 升等 is cut into 升 and 等 by jieba; the hit must still contain the word itself.
    assert env.search("升等") == env.search('"升等"') == TRAD_SIMP_KEYWORD_DOCS
    assert "zh/unrelated.txt" not in env.search("升等")


def test_lang_01_hits_are_highlighted_at_the_matched_text(env):
    for path, item in env.results("升等").items():
        marked = re.findall(
            r">([^<]+)</mark>", " ".join(s for g in item.segments for s in g.snippets)
        )
        assert marked, path
        assert all("升等" in m or m in "升等" for m in marked), (path, marked)


def test_lang_02_word_order_of_chinese_terms_is_not_significant(env):
    # Recorded behaviour: terms are ANDed, so 記錄會議 finds the same documents as 會議記錄.
    plain = env.search("會議記錄")
    assert plain >= {"zh/trad_meeting.txt", "zh/simp_meeting.txt", SYMBOLS}
    assert env.search("記錄會議") == plain


def test_lang_03_case_variants_agree_when_case_is_ignored(env):
    outcomes = [env.results(q) for q in ("outstanding", "OUTSTANDING", "Outstanding")]
    assert set(outcomes[0]) == set(outcomes[1]) == set(outcomes[2]) == {"en/cases.txt", SYMBOLS}
    counts = [{p: i.total_matches for p, i in o.items()} for o in outcomes]
    assert counts[0] == counts[1] == counts[2]


def test_lang_04_match_case_returns_only_exact_case(env):
    assert env.search("outstanding", match_case=True) == {"en/cases.txt", SYMBOLS}
    assert env.search("OUTSTANDING", match_case=True) == {"en/cases.txt"}
    assert env.search("Outstanding", match_case=True) == {"en/cases.txt"}
    assert env.search("oUtStAnDiNg", match_case=True) == set()


def test_lang_05_whole_word_skips_words_that_merely_contain_the_term(scratch, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "plural.txt").write_text("Only outstandings remain.", encoding="utf-8")
    (tmp_path / "docs" / "single.txt").write_text("One outstanding result.", encoding="utf-8")
    scratch.run([tmp_path / "docs"])
    # Stemming: every form finds every document. Whole word asks for the exact word only.
    assert scratch.names("outstanding") == {"single.txt", "plural.txt"}
    assert scratch.names("outstanding", whole_word=True) == {"single.txt"}
    assert scratch.names("outstandings", whole_word=True) == {"plural.txt"}


def test_lang_07_english_word_forms_match_each_other(env):
    # Decided 2026-09-30 (OQ-2): missing stemming is not acceptable. SQLite's porter tokenizer
    # indexes outstand / outstanding / outstandings under one stem.
    expected = {"en/cases.txt", SYMBOLS}
    assert (
        env.search("outstand")
        == env.search("outstandings")
        == env.search("outstanding")
        == expected
    )
    assert env.search("Outstanding") == expected


def test_lang_07_exact_options_switch_stemming_off(env):
    assert env.search("outstand", whole_word=True) == {"en/cases.txt"}  # the literal word outstand
    # match case is a literal check too: "outstanding" is in both files, "Outstanding" only one.
    assert env.search("outstanding", match_case=True) == {"en/cases.txt", SYMBOLS}
    assert env.search("Outstanding", match_case=True) == {"en/cases.txt"}


def test_lang_11_boolean_operators(env):
    assert env.search("升等 AND 會議記錄") == TRAD_SIMP_KEYWORD_DOCS
    assert env.search("outstanding NOT 升等") == {"en/cases.txt"}
    assert env.search("升等 OR QATREEdeep") == TRAD_SIMP_KEYWORD_DOCS | {"tree/sub/deep/c.txt"}
    assert (
        env.search("升等 and 會議記錄") == TRAD_SIMP_KEYWORD_DOCS
    )  # lower case is an operator too
    assert env.search("升等 NOT 會議記錄") == set()


@pytest.mark.parametrize(
    "query,code",
    [
        ('"升等', "unpaired_phrase"),
        ("AND 升等", "operator_position"),
        ("升等 AND OR 會議", "repeated_operator"),
    ],
)
def test_lang_11_malformed_boolean_queries_give_a_clear_error(env, query, code):
    with pytest.raises(SearchQueryError) as exc:
        env.searcher.search(query)
    assert exc.value.code == code


def test_lang_12_regex_mode(env):
    assert env.search(r"2023-\d{2}-\d{2}", regex=True) == {SYMBOLS}
    assert env.search(r"QATREE(root|sub)", regex=True) == {"tree/a.txt", "tree/sub/b.txt"}
    with pytest.raises(SearchQueryError) as exc:
        env.searcher.search("(", regex=True)
    assert exc.value.code == "regex_syntax"


def test_lang_12_nested_repeat_pattern_stops_promptly(env):
    # Catastrophic-pattern coverage on a pathological corpus is in tests/performance/.
    started = time.monotonic()
    try:
        env.searcher.search(r"(a+)+$", regex=True)
    except SearchQueryError as exc:
        assert exc.code == "regex_timeout"
    assert time.monotonic() - started < 10


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_lang_13_empty_queries_return_nothing(env, query):
    assert env.search(query) == set()


def test_lang_13_very_long_query_does_not_crash_or_hang(env):
    started = time.monotonic()
    assert env.search("升等 " * 3000) == TRAD_SIMP_KEYWORD_DOCS
    assert time.monotonic() - started < 10


def test_lang_14_mixed_chinese_and_english_terms(env):
    assert env.search("升等 outstanding") == {SYMBOLS}
    assert env.search("outstanding，") == set()  # the comma must follow the word in the text
    assert env.search("「引號」") == {SYMBOLS}
    assert env.search("會議記錄，") == {SYMBOLS}
