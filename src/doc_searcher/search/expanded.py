# Purpose: Explicit synonym/fuzzy/hybrid retrieval while preserving literal-search contracts.
# Behavior: Union all exact hits with bounded expansions and dense passages, then paginate.
# Usage: Original literal occurrences remain separate from nonliteral passage provenance.
import base64
import hashlib
import json
import re
from .expansion import simple_query, synonym_terms, fuzzy_terms, load_synonyms
from doc_searcher.indexing.scanner import FileScanner
from .searcher import SearchResultItem, SearchPage, SearchQueryError


def search_page(
    searcher, query, *, mode, cursor=None, limit=200, synonyms=None, synonym_scopes=None, **options
):
    if mode not in {"expanded", "hybrid"}:
        raise ValueError("Unknown search mode")
    if limit < 1:
        raise ValueError("limit must be positive")
    if not simple_query(query) or options.get("regex"):
        raise ValueError(
            "Expanded/hybrid mode accepts simple queries; use literal mode for quotes, boolean, filename or regex syntax."
        )
    if synonyms is None and synonym_scopes is None:
        synonyms, synonym_scopes = load_synonyms()
    semantic = None
    if mode == "hybrid":
        from .semantic import SemanticIndex

        semantic = SemanticIndex(searcher.db)
        if not semantic.ready():
            semantic.rebuild(options.get("cancel_check"))
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                query,
                mode,
                semantic.encoder.model_id if semantic else None,
                synonyms,
                synonym_scopes,
                {key: value for key, value in options.items() if key != "cancel_check"},
            ],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    conn = searcher.db.get_connection()
    with conn:
        conn.execute("BEGIN")
        revision = searcher.db.revision()
        after = None
        if cursor:
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor))
                if token[:2] != [revision, fingerprint]:
                    raise ValueError()
                after = tuple(token[2])
            except (ValueError, TypeError, IndexError) as exc:
                raise SearchQueryError(
                    "Index or query changed; search again.", "stale_cursor"
                ) from exc
        exact = searcher.search(query, limit=2**63 - 1, **options)
        items = {item.doc_id: item for item in exact}
        scores = {item.doc_id: 1 / (60 + rank) for rank, item in enumerate(exact, 1)}
        for item in exact:
            item.matched_by = ["literal"]
        clause, params = searcher._build_filter_clause(
            options.get("type_filter", "all"),
            options.get("modified_after"),
            options.get("modified_before"),
            options.get("min_size"),
            options.get("max_size"),
            options.get("date_field", "mtime"),
            options.get("include_paths"),
            options.get("exclude_patterns"),
            options.get("search_roots"),
        )
        terms = [(term, "synonym", None) for term in synonym_terms(query, synonyms or {})]
        for scope in synonym_scopes or []:
            if (
                not isinstance(scope, dict)
                or not set(scope) <= {"file_type", "path", "terms"}
                or any(
                    key in scope and not isinstance(scope[key], str)
                    for key in ("file_type", "path")
                )
            ):
                raise ValueError("Invalid synonym scope; use file_type/path/terms")
            terms.extend(
                (term, "synonym", scope) for term in synonym_terms(query, scope.get("terms", {}))
            )
        # Vocabulary comes from stored text; never let tokenizer omissions define expansions.
        rows = conn.execute(
            "SELECT s.*, d.path, d.file_type FROM doc_segments s "
            "JOIN documents d ON d.id=s.doc_id WHERE 1=1 " + clause,
            params,
        ).fetchall()
        if not options.get("match_case") and not options.get("whole_word"):
            vocabulary = set()
            for row in rows:
                vocabulary.update(re.findall(r"\b[a-zA-Z]{4,}\b", row["content"].lower()))
            terms.extend((term, "fuzzy", None) for term in fuzzy_terms(query, vocabulary))

        def add_passage(row, reason, start, end, score, term=None):
            doc_id = row["doc_id"]
            if doc_id not in items:
                doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
                items[doc_id] = SearchResultItem(
                    doc_id,
                    doc["path"],
                    doc["filename"],
                    doc["file_type"],
                    doc["file_size"],
                    doc["mtime"],
                    0,
                    0,
                    ctime=doc["ctime"],
                    parse_status=doc["parse_status"],
                    warnings=json.loads(doc["warnings"]),
                    matched_by=[],
                )
            item = items[doc_id]
            if reason not in item.matched_by:
                item.matched_by.append(reason)
            passage = dict(
                segment_row_id=row["id"] if "id" in row.keys() else row["segment_row_id"],
                segment_id=row["segment_id"],
                segment_type=row["segment_type"],
                start=start,
                end=end,
                text=row["content"][start:end],
                matched_by=reason,
                term=term,
            )
            if "sources" in row.keys():
                passage["sources"] = [
                    span["source"]
                    for span in json.loads(row["sources"])
                    if span["start"] < end and span["end"] > start
                ][:32]
            if len(item.passages) < 3 and not any(
                p["segment_row_id"] == passage["segment_row_id"]
                and p["start"] < end
                and p["end"] > start
                for p in item.passages
            ):
                item.passages.append(passage)
            scores[doc_id] = scores.get(doc_id, 0) + score

        for row in rows:
            if options.get("cancel_check") and options["cancel_check"]():
                raise SearchQueryError("Search cancelled.", "cancelled")
            for term, reason, scope in terms:
                if scope and (
                    scope.get("file_type") not in (None, row["file_type"])
                    or scope.get("path")
                    and not FileScanner.is_path_within_directory(row["path"], scope["path"])
                ):
                    continue
                if not searcher._literal_matches(
                    row["content"],
                    term,
                    options.get("match_case", False),
                    options.get("whole_word", False),
                ):
                    continue
                from .matches import iter_locations

                occurrence = next(
                    iter_locations(
                        row["content"],
                        [term],
                        match_case=options.get("match_case", False),
                        whole_word=options.get("whole_word", False),
                    ),
                    None,
                )
                if occurrence is None:
                    continue
                start = occurrence.start
                add_passage(
                    row,
                    reason,
                    max(0, start - 120),
                    min(len(row["content"]), occurrence.end + 120),
                    1 / 1000,
                    term,
                )
        if semantic:
            for rank, (score, row) in enumerate(
                semantic.search(query, clause, params, cancel_check=options.get("cancel_check")), 1
            ):
                if score >= 0.65:
                    # Return only original source text; conceptual relevance has no literal hit.
                    data = {**row, "id": row["segment_row_id"]}
                    add_passage(data, "semantic", row["start"], row["end"], 1 / (60 + rank))
        ordered = list(items.values())
        for item in ordered:
            # Literal matches are exhaustive and always precede expansion-only documents.
            item.rank_score = (-2 if "literal" in item.matched_by else 0) - min(
                1, scores.get(item.doc_id, 0)
            )
        ordered.sort(key=lambda item: (item.rank_score, item.doc_id))
        total = len(ordered)
        if after is not None:
            ordered = [item for item in ordered if (item.rank_score, item.doc_id) > after]
        more = len(ordered) > limit
        selected = ordered[:limit]
        next_cursor = None
        if more:
            last = selected[-1]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps([revision, fingerprint, [last.rank_score, last.doc_id]]).encode()
            ).decode()
        return SearchPage(
            selected,
            next_cursor,
            more,
            total,
            True,
            revision,
            retrieval_mode=mode,
            semantic_ready=bool(semantic),
            semantic_candidate_limit=1000 if semantic else None,
        )
