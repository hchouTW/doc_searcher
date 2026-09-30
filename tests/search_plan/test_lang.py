"""Language and query cases from docs/test-plan.md, run against the generated dataset."""

import pytest

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
