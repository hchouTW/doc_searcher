# Purpose: Explicit, bounded domain-synonym and typo expansion for simple literal queries.
# Behavior: Distance-one words only; never fuzz numbers, code IDs, short CJK or boolean syntax.
# Usage: Synonyms map terms to lists; scoped dictionaries include path/file_type selectors.
import re
import json
import os
from functools import lru_cache
from doc_searcher.search.script_fold import fold


def distance_one(left, right):
    """True for one insertion, deletion or substitution (Levenshtein distance one)."""
    if abs(len(left) - len(right)) > 1 or left == right:
        return False
    if len(left) > len(right):
        left, right = right, left
    i = j = edits = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
        else:
            edits += 1
            if edits > 1:
                return False
            if len(left) == len(right):
                i += 1
            j += 1
    return edits + (len(right) - j) + (len(left) - i) == 1


def simple_query(query):
    return bool(query and not re.search(r'["():]|\b(?:AND|OR|NOT)\b', query, re.I))


def synonym_terms(query, synonyms):
    if not isinstance(synonyms, dict):
        raise ValueError("Synonyms must map terms to string lists")
    terms = []
    for key, values in synonyms.items():
        if (
            not isinstance(key, str)
            or not isinstance(values, list)
            or any(not isinstance(v, str) for v in values)
        ):
            raise ValueError("Synonyms must map terms to string lists")
        group = [key, *values]
        if fold(query).casefold() in [fold(v).casefold() for v in group]:
            terms.extend(v for v in group if fold(v).casefold() != fold(query).casefold())
    return list(dict.fromkeys(terms))[:32]


def fuzzy_terms(query, vocabulary):
    if not re.fullmatch(r"[a-zA-Z]{4,}", query):
        return []
    query = query.lower()
    return [word for word in sorted(vocabulary) if distance_one(query, word)][:32]


@lru_cache(maxsize=4)
def _read_synonyms(path, timestamp):
    with open(path, encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("Synonym file must be a JSON object")
    terms, scopes = value.get("terms", {}), value.get("scopes", [])
    synonym_terms("", terms)  # validate before queries consume the file
    if not isinstance(scopes, list):
        raise ValueError("Synonym scopes must be a list")
    return terms, scopes


def load_synonyms(path=None):
    """Optional local JSON dictionary configured by DOC_SEARCHER_SYNONYMS."""
    path = path or os.environ.get("DOC_SEARCHER_SYNONYMS")
    if not path:
        return {}, []
    try:
        return _read_synonyms(path, os.stat(path).st_mtime_ns)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Cannot read synonym dictionary: " + str(exc)) from exc
