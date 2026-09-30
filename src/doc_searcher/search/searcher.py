# Purpose: Full-text search engine, query builder, and result ranker.
# What the code does:
#   - Translates natural queries, boolean expressions, and quoted phrases into FTS5 match syntax.
#   - Supports explicit filename searches and user-facing query validation.
#   - Applies file type, mtime/ctime, size, include-path, and exclusion filters safely.
#     With search_roots, relative exclusion patterns only apply below the search folders.
#   - Supports case-sensitive, whole-word, and validated regular-expression modes; regex
#     searches run under a time budget and can be cancelled (search.regex_engine).
#   - Executes FTS queries against tokenized and raw content with BM25 ranking.
#   - Punctuation-only terms (-, _, (, /) never reach the FTS expression, because the index holds
#     no such tokens and an AND with them matches nothing. Queries containing punctuation are
#     narrowed by FTS on their words, then confirmed by a literal check on the stored text, as are
#     Chinese terms jieba cannot segment into words (升等 would otherwise match 升 and 等 apart);
#     a query made only of punctuation is answered with the LIKE search.
#   - English word forms match each other (the index uses SQLite's porter tokenizer); match case
#     and whole word ask for the exact word and switch that off, as does filename search.
#   - Folds Simplified/Traditional Chinese (search.script_fold) in queries, literal re-checks,
#     filename search and the LIKE fallback; regex mode matches stored text exactly as written.
#   - Uses a folded CJK gram index with segment-scoped boolean verification and stable
#     document pagination; page cursors invalidate when the index changes.
#   - Generates snippet contexts with highlighted HTML <mark> tags and location indicators.
# Usage notes, dependencies, or assumptions:
#   - Uses SQLite FTS5 functions and doc_searcher.search.text_helper.

import bisect
import base64
import hashlib
import json
import itertools
import html
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional
import jieba

from doc_searcher.search.matches import (iter_locations, occurrence_snippets, MatchLocation, MatchPage, render_context)
from doc_searcher.search.cjk_index import candidate_expression
from doc_searcher.search.query import parse_query, QuerySyntaxError
from doc_searcher.storage.database import Database
from doc_searcher.storage.errors import classify
from doc_searcher.platform.paths import canonical_path, canonical_text
from doc_searcher.search.script_fold import fold, script_regex
from doc_searcher.search.stemming import matches_stem, stem_regex_source, stem_spec
from doc_searcher.search.regex_engine import (
    Deadline,
    RegexError,
    compile_user_regex,
)
from doc_searcher.indexing.scanner import is_absolute_pattern
from doc_searcher.search.text_helper import (
    generate_highlighted_snippets,
    has_word_char,
)


logger = logging.getLogger(__name__)


@dataclass
class SegmentMatch:
    segment_id: str
    segment_type: str
    snippets: List[str]
    segment_row_id: int = 0
    match_count: int = 0


@dataclass
class SearchResultItem:
    doc_id: int
    path: str
    filename: str
    file_type: str
    file_size: int
    mtime: float
    rank_score: float
    total_matches: int
    segments: List[SegmentMatch] = field(default_factory=list)
    ctime: float = 0.0
    count_complete: bool = True
    parse_status: str = "unknown"
    warnings: List[dict] = field(default_factory=list)

    @property
    def segment_count(self):
        return len(self.segments)

    @property
    def snippet_count(self):
        return sum(len(s.snippets) for s in self.segments)


@dataclass
class SearchPage:
    items: List[SearchResultItem]
    next_cursor: Optional[str]
    has_more: bool
    total_documents: Optional[int]
    complete: bool
    revision: int


class SearchQueryError(ValueError):
    """Raised when a user query has invalid boolean, quote, or regex syntax.

    code is stable for callers (UI translation, tests): unpaired_phrase, operator_position,
    repeated_operator, filename_empty, filename_quotes, regex_syntax, regex_timeout, cancelled. The message is the
    Traditional Chinese text shown when no translation is available.
    """

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


# SQLite reports FTS5 query-language problems (not database faults) with these messages.
_FTS_QUERY_ERRORS = (
    "fts5: syntax error",
    "unterminated string",
    "no such column",
    "malformed match",
)


def _is_cjk(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _is_fts_query_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return any(fragment in message for fragment in _FTS_QUERY_ERRORS)


class DocumentSearcher:
    """Executes full-text queries and formats ranked search results."""

    def __init__(self, db: Database):
        self.db = db

    def search(self, *args, **options):
        """Search in one consistent snapshot, including during background index updates."""
        conn = self.db.get_connection()
        try:
            if conn.in_transaction:
                return self._attach_quality(self._search(*args, **options))
            with conn:
                conn.execute("BEGIN")
                return self._attach_quality(self._search(*args, **options))
        except sqlite3.OperationalError as exc:
            error = classify(exc, self.db.db_path)
            if error is not None:
                raise error from exc
            raise

    def _attach_quality(self, items):
        for item in items:
            row = self.db.get_connection().execute("SELECT parse_status, warnings FROM documents WHERE id=?", (item.doc_id,)).fetchone()
            item.parse_status = row["parse_status"]
            item.warnings = json.loads(row["warnings"])
        return items

    def _search(
        self,
        query_str: str,
        type_filter: str = "all",
        limit: int = 200,
        modified_after: Optional[float] = None,
        modified_before: Optional[float] = None,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
        date_field: str = "mtime",
        include_paths: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        match_case: bool = False,
        whole_word: bool = False,
        regex: bool = False,
        search_roots: Optional[List[str]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        page_after=None,
        page_meta=None,
    ) -> List[SearchResultItem]:
        """Search documents matching query string and optional metadata filters.

        Regex searches stop with SearchQueryError code "regex_timeout" after
        REGEX_TIME_BUDGET_SECONDS, and with code "cancelled" once cancel_check() returns True.
        """
        clean_query = query_str.strip()
        if not clean_query:
            return []

        filename_query = None if regex else self._extract_filename_query(clean_query)
        filter_clause, filter_params = self._build_filter_clause(
            type_filter,
            modified_after,
            modified_before,
            min_size,
            max_size,
            date_field,
            include_paths,
            exclude_patterns,
            search_roots,
        )
        if regex:
            return self._search_regex(
                clean_query,
                filter_clause,
                filter_params,
                limit,
                match_case,
                whole_word,
                cancel_check, page_after, page_meta,
            )
        negative_only = re.fullmatch(r'NOT\s+(?:"([^"]+)"|(\S+))', clean_query, re.IGNORECASE)
        if negative_only:
            return self._search_negative_only(
                negative_only.group(1) or negative_only.group(2),
                filter_clause,
                filter_params,
                limit,
                match_case,
                whole_word,
            )
        if filename_query is not None:
            return self._search_filenames(
                filename_query,
                filter_clause,
                filter_params,
                limit,
                match_case,
                whole_word,
            )

        parsed = self._parse(clean_query)
        conn = self.db.get_connection()
        candidates = {}
        ranks: dict[int, float] = {}
        for term in parsed.root.terms():
            hits: set[int] = set()
            grams = candidate_expression(term)
            if grams:
                hits.update(r[0] for r in conn.execute(
                    "SELECT rowid FROM doc_cjk_fts WHERE doc_cjk_fts MATCH ?", (grams,)))
            else:
                expression = self._build_fts5_query('"' + term + '"')
                if expression:
                    try:
                        for row in conn.execute("SELECT s.id, bm25(doc_fts) AS rank FROM doc_fts f "
                            "JOIN doc_segments s ON s.doc_id = CAST(f.doc_id AS INTEGER) "
                            "AND s.segment_id = f.segment_id AND s.segment_type = f.segment_type "
                            "WHERE doc_fts MATCH ?", (expression,)):
                            hits.add(row[0])
                            ranks[row[0]] = min(ranks.get(row[0], 0), row[1])
                    except sqlite3.OperationalError as exc:
                        if not _is_fts_query_error(exc):
                            raise
                        logger.info("FTS query %r rejected (%s); using stored-text scan", expression, exc)
                        hits.update(r[0] for r in conn.execute("SELECT id FROM doc_segments"))
                else:
                    hits.update(r[0] for r in conn.execute("SELECT id FROM doc_segments"))
            # Only verified exclusions may be subtracted: grams are a candidate superset.
            verified = set()
            for sid in hits:
                content = conn.execute("SELECT content FROM doc_segments WHERE id = ?", (sid,)).fetchone()[0]
                if self._literal_matches(content, term, match_case, whole_word):
                    verified.add(sid)
            candidates[term] = verified
        selected = parsed.root.evaluate(candidates.__getitem__)
        def rows():
            for sid in sorted(selected):
                if cancel_check and cancel_check():
                    raise SearchQueryError("搜尋已取消。", "cancelled")
                row = conn.execute(f"SELECT s.id AS segment_row_id, s.doc_id, s.segment_id, "
                    f"s.segment_type, s.content, 0.0 AS rank, d.path, d.filename, d.file_type, "
                    f"d.file_size, d.mtime, d.ctime FROM doc_segments s JOIN documents d ON d.id=s.doc_id "
                    f"WHERE s.id = ? {filter_clause}", [sid, *filter_params]).fetchone()
                if row:
                    data = dict(row)
                    data["rank"] = ranks.get(sid, 0.0)
                    yield data
        keywords = [t for t in parsed.root.terms() if t]
        return self._aggregate_rows(rows(), keywords, limit, clean_query, match_case, whole_word,
                                    cancel_check=cancel_check, page_after=page_after, page_meta=page_meta)

    @staticmethod
    def _parse(query):
        try:
            return parse_query(query)
        except QuerySyntaxError as exc:
            messages = {"unpaired_phrase": "精確片語的雙引號未成對。",
                        "operator_position": "AND、OR、NOT 前後都必須有搜尋詞。",
                        "repeated_operator": "AND、OR、NOT 不可連續使用。"}
            raise SearchQueryError(messages[exc.code], exc.code) from exc

    def search_page(self, query_str, *, cursor=None, limit=200, **options):
        if limit < 1:
            raise ValueError("limit must be positive")
        fingerprint = hashlib.sha256(json.dumps([query_str, {k: v for k, v in options.items() if k != "cancel_check"}], sort_keys=True,
            default=str).encode()).hexdigest()
        conn = self.db.get_connection()
        # All reads in a page share a SQLite snapshot while other threads may update the index.
        with conn:
            conn.execute("BEGIN")
            revision = self.db.revision()
            after = None
            if cursor:
                try:
                    token = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                    if token[0] != revision or token[1] != fingerprint:
                        raise ValueError()
                    after = tuple(token[2])
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    raise SearchQueryError("索引或搜尋條件已變更，請重新搜尋。", "stale_cursor") from exc
            meta = {}
            special = not options.get("regex") and (self._extract_filename_query(query_str.strip()) is not None
                or re.fullmatch(r'NOT\s+(?:"[^\"]+"|\S+)', query_str.strip(), re.IGNORECASE))
            if special:
                items = self.search(query_str, limit=2**63-1, **options)
                items.sort(key=lambda x: (x.rank_score, x.doc_id))
                total = len(items)
                if after is not None:
                    items = [x for x in items if (x.rank_score, x.doc_id) > after]
                more = len(items) > limit
                items = items[:limit]
            else:
                items = self.search(query_str, limit=limit, page_after=after, page_meta=meta, **options)
                total, more = meta.get("total", 0), meta.get("more", False)
            next_cursor = None
            if more:
                last = items[-1]
                next_cursor = base64.urlsafe_b64encode(json.dumps(
                    [revision, fingerprint, [last.rank_score, last.doc_id]]).encode()).decode()
            return SearchPage(items, next_cursor, more, total, True, revision)

    def match_locations(self, query_str, doc_id, *, offset=0, limit=100, revision=None, **options):
        """Count a selected document and return a bounded page of original-text locations."""
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("Invalid location page")
        conn = self.db.get_connection()
        if not conn.in_transaction:
            with conn:
                conn.execute("BEGIN")
                return self.match_locations(query_str, doc_id, offset=offset, limit=limit,
                                            revision=revision, **options)
        current = self.db.revision()
        if revision is not None and current != revision:
            raise SearchQueryError("索引已變更，請重新搜尋。", "stale_cursor")
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            raise SearchQueryError("文件已移除，請重新搜尋。", "stale_cursor")
        match_case, whole_word = options.get("match_case", False), options.get("whole_word", False)
        regex_pattern, deadline = None, None
        filename = None if options.get("regex") else self._extract_filename_query(query_str)
        negative = not options.get("regex") and re.fullmatch(r'NOT\s+(?:"[^\"]+"|\S+)', query_str, re.I)
        if options.get("regex"):
            try:
                regex_pattern = compile_user_regex(query_str, match_case, whole_word)
            except RegexError as exc:
                raise SearchQueryError(str(exc), "regex_syntax") from exc
            deadline = Deadline()
        parsed = None if regex_pattern is not None or filename or negative else self._parse(query_str)
        rows = [dict(id=0, segment_id="", segment_type="檔名", content=doc["filename"])] if filename else conn.execute(
            "SELECT * FROM doc_segments WHERE doc_id=? ORDER BY id", (doc_id,))
        locations, total = [], 0
        try:
            for row in rows:
                if negative:
                    continue
                text = row["content"]
                source_spans = None
                source_starts = None
                terms = [filename] if filename else [] if parsed is None else parsed.root.positives(
                    lambda term, content=text: self._literal_matches(content, term, match_case, whole_word))
                for hit in iter_locations(text, terms, regex_pattern=regex_pattern, deadline=deadline,
                        cancel_check=options.get("cancel_check"), match_case=match_case,
                        whole_word=whole_word, stemming=not bool(filename)):
                    if offset <= total < offset + limit:
                        source = None
                        if "sources" in row.keys():
                            if source_spans is None:
                                source_spans = json.loads(row["sources"])
                                source_starts = [span["start"] for span in source_spans]
                            index = max(0, bisect.bisect_right(source_starts, hit.start) - 1)
                            found_sources = []
                            for span in itertools.islice(source_spans, index, None):
                                if span["start"] > hit.end or (span["start"] >= hit.end and hit.start != hit.end):
                                    break
                                if span["start"] <= hit.start < span["end"] or (span["start"] < hit.end and span["end"] > hit.start):
                                    found_sources.append(span["source"])
                            if found_sources:
                                source = found_sources[0]
                                if len(found_sources) > 1:
                                    source = dict(kind="range", locations=found_sources,
                                        location=" → ".join(x.get("location", str(x)) for x in found_sources))
                        locations.append(MatchLocation(hit.start, hit.end, hit.matched_terms,
                            doc_id, row["id"], row["segment_id"], row["segment_type"], source, current))
                    total += 1
        except TimeoutError as exc:
            raise SearchQueryError("正規表示式超過時間限制。", "regex_timeout") from exc
        return MatchPage(locations, offset + limit if offset + limit < total else None, total, True, current)

    def match_context(self, location, *, context_chars=120, full_segment=False, text_offset=0, text_limit=2048):
        """Return bounded original context, rejecting deleted or replaced segment identities."""
        if isinstance(location, dict):
            location = MatchLocation(**location)
        if not 0 <= context_chars <= 2000 or not 1 <= text_limit <= 10000 or text_offset < 0:
            raise ValueError("Invalid context range")
        conn = self.db.get_connection()
        if not conn.in_transaction:
            with conn:
                conn.execute("BEGIN")
                return self.match_context(location, context_chars=context_chars, full_segment=full_segment,
                                          text_offset=text_offset, text_limit=text_limit)
        if location.revision != self.db.revision():
            raise SearchQueryError("索引已變更，請重新搜尋。", "stale_cursor")
        if location.segment_row_id:
            row = conn.execute("SELECT content FROM doc_segments WHERE id=? AND doc_id=?",
                               (location.segment_row_id, location.doc_id)).fetchone()
        else:
            row = conn.execute("SELECT filename AS content FROM documents WHERE id=?", (location.doc_id,)).fetchone()
        if row is None:
            raise SearchQueryError("文件已移除，請重新搜尋。", "stale_cursor")
        content = row["content"]
        if not 0 <= location.start <= location.end <= len(content):
            raise ValueError("Invalid location offsets")
        start = text_offset if full_segment else max(0, location.start - context_chars)
        end = min(len(content), start + text_limit) if full_segment else min(len(content), location.start + 300 + context_chars, location.end + context_chars)
        return dict(text=content[start:end], start=start, end=end, active_start=location.start,
                    active_end=location.end, html=render_context(content, [location], start=start, end=end,
                    active=(location.start, location.end)), segment_id=location.segment_id,
                    segment_type=location.segment_type, source=location.source, revision=location.revision,
                    next_offset=end if end < len(content) else None)

    def _aggregate_rows(
        self,
        rows: Iterable[Any],
        keywords: List[str],
        limit: int,
        query: str,
        match_case: bool = False,
        whole_word: bool = False,
        regex_pattern: Optional[Any] = None,
        deadline: Optional[Deadline] = None,
        verify_literal: bool = False,
        cancel_check=None, page_after=None, page_meta=None,
    ) -> List[SearchResultItem]:
        """Aggregate matching segment rows into ranked document results.

        verify_literal re-checks every term against the stored text (as match_case and
        whole_word already do); used when FTS could only search part of a term.
        """
        groups: dict[int, list] = {}
        parsed = self._parse(query) if regex_pattern is None else None
        for row in rows:
            if cancel_check and cancel_check():
                raise SearchQueryError("搜尋已取消。", "cancelled")
            content = row["content"]
            if regex_pattern is not None:
                if regex_pattern.search(content, **Deadline.timeout_kwargs(deadline)) is None:
                    continue
            elif parsed is not None and verify_literal and not parsed.root.evaluate(lambda term, text=content: self._literal_matches(
                    text, term, match_case, whole_word)):
                continue
            doc_id = int(row["doc_id"])
            stored = dict(row)
            if stored.get("segment_row_id"):
                stored.pop("content", None)  # retain identities, not every candidate's original text
            groups.setdefault(doc_id, []).append(stored)
        ranking = sorted(groups, key=lambda doc_id: (min(float(r["rank"]) for r in groups[doc_id]), doc_id))
        if page_meta is not None:
            page_meta["total"] = len(ranking)
        if page_after is not None:
            ranking = [doc_id for doc_id in ranking if
                       (min(float(r["rank"]) for r in groups[doc_id]), doc_id) > page_after]
        if page_meta is not None:
            page_meta["more"] = len(ranking) > limit
        results = []
        for doc_id in ranking[:limit]:
            first = groups[doc_id][0]
            item = SearchResultItem(doc_id, first["path"], first["filename"], first["file_type"],
                first["file_size"], first["mtime"], min(float(r["rank"]) for r in groups[doc_id]),
                0, ctime=first["ctime"])
            for row in groups[doc_id]:
                content = row.get("content")
                if content is None:
                    content = self.db.get_connection().execute("SELECT content FROM doc_segments WHERE id=?",
                        (row["segment_row_id"],)).fetchone()[0]
                terms = keywords if parsed is None else parsed.root.positives(
                    lambda term, text=content: self._literal_matches(text, term, match_case, whole_word))
                options = dict(regex_pattern=regex_pattern, deadline=deadline, cancel_check=cancel_check,
                               match_case=match_case, whole_word=whole_word)
                count = sum(1 for _ in iter_locations(content, terms, **options))
                snippets = occurrence_snippets(content, terms, **options)
                sid = row["segment_row_id"] if "segment_row_id" in row.keys() else 0
                item.total_matches += count
                item.segments.append(SegmentMatch(str(row["segment_id"]), row["segment_type"], snippets, sid, count))
            results.append(item)
        return results

    @staticmethod
    def _build_type_clause(type_filter: str) -> str:
        """Build the fixed SQL clause for a supported file-type filter."""
        if isinstance(type_filter, (list, tuple)):
            parts = [DocumentSearcher._build_type_clause(t).removeprefix("AND ") for t in dict.fromkeys(type_filter)]
            return "AND (" + " OR ".join(parts) + ")" if parts else ""
        type_clause = ""
        type_filter_lower = type_filter.lower()
        if type_filter_lower == "pdf":
            type_clause = "AND d.file_type = 'pdf'"
        elif type_filter_lower in {"word", "doc"}:
            type_clause = "AND d.file_type IN ('docx', 'doc')"
        elif type_filter_lower in {"excel", "xls"}:
            type_clause = "AND d.file_type IN ('xlsx', 'xls')"
        elif type_filter_lower in {"ppt", "powerpoint"}:
            type_clause = "AND d.file_type IN ('pptx', 'ppt')"
        elif type_filter_lower == "text":
            type_clause = "AND d.file_type IN ('txt', 'md', 'csv')"
        return type_clause

    @classmethod
    def _build_filter_clause(
        cls,
        type_filter: str,
        modified_after: Optional[float],
        modified_before: Optional[float],
        min_size: Optional[int],
        max_size: Optional[int],
        date_field: str = "mtime",
        include_paths: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        search_roots: Optional[List[str]] = None,
    ) -> tuple[str, List[Any]]:
        """Build a safe SQL metadata filter clause and its bound parameters."""
        clauses = []
        type_clause = cls._build_type_clause(type_filter)
        if type_clause:
            clauses.append(type_clause.removeprefix("AND "))

        params: List[Any] = []
        date_column = "d.ctime" if date_field == "ctime" else "d.mtime"
        for value, expression in (
            (modified_after, f"{date_column} >= ?"),
            (modified_before, f"{date_column} < ?"),
            (min_size, "d.file_size >= ?"),
            (max_size, "d.file_size <= ?"),
        ):
            if value is not None:
                clauses.append(expression)
                params.append(value)

        normalized_paths = [canonical_path(path) for path in (include_paths or []) if path]
        if normalized_paths:
            path_clauses = []
            for path in normalized_paths:
                escaped = cls._escape_like(path.rstrip("/\\"))
                path_clauses.append("(d.path = ? OR d.path LIKE ? ESCAPE '\\')")
                # Escape the separator too: on Windows it is "\\", the LIKE escape character.
                params.extend([path.rstrip("/\\"), f"{escaped}{cls._escape_like(os.sep)}%"])
            clauses.append("(" + " OR ".join(path_clauses) + ")")

        # Relative patterns see only the part below the containing search root (starting with a
        # separator), so folders above a search folder never exclude it. Longest root first.
        roots = sorted(
            {canonical_path(root).rstrip("/\\") for root in (search_roots or []) if root},
            key=len,
            reverse=True,
        )
        relative_sql = "d.path"
        relative_params: List[Any] = []
        if roots:
            cases = []
            for root in roots:
                cases.append("WHEN d.path LIKE ? ESCAPE '\\' THEN SUBSTR(d.path, ?)")
                relative_params.extend(
                    [f"{cls._escape_like(root)}{cls._escape_like(os.sep)}%", len(root) + 1]
                )
            relative_sql = "CASE " + " ".join(cases) + " ELSE d.path END"

        for raw_pattern in exclude_patterns or []:
            pattern = canonical_text(raw_pattern.strip().replace("\\", "/"))
            if not pattern:
                continue
            escaped = cls._escape_like(pattern).replace("*", "%").replace("?", "_")
            if is_absolute_pattern(pattern):
                clauses.append("REPLACE(d.path, '\\', '/') NOT LIKE ? ESCAPE '\\'")
                params.append(f"{escaped}%")
                continue
            clauses.append(f"REPLACE({relative_sql}, '\\', '/') NOT LIKE ? ESCAPE '\\'")
            params.extend(relative_params)
            if "/" not in pattern and not any(char in pattern for char in "*?"):
                params.append(f"%/{escaped}/%")
            else:
                params.append(f"%{escaped}%")

        clause = "" if not clauses else "AND " + " AND ".join(clauses)
        return clause, params

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _extract_filename_query(query: str) -> Optional[str]:
        """Extract a filename:/檔名: query, or return None for content search."""
        match = re.match(r"^(?:filename|檔名)\s*:\s*", query, re.IGNORECASE)
        if not match:
            return None
        filename_query = query[match.end() :].strip()
        if not filename_query:
            raise SearchQueryError("請在 filename: 或 檔名: 後輸入檔名關鍵字。", "filename_empty")
        if filename_query.startswith('"') or filename_query.endswith('"'):
            if not (
                len(filename_query) >= 2
                and filename_query.startswith('"')
                and filename_query.endswith('"')
            ):
                raise SearchQueryError("檔名搜尋的雙引號未成對。", "filename_quotes")
            filename_query = filename_query[1:-1].strip()
        return filename_query

    def _search_filenames(
        self,
        filename_query: str,
        filter_clause: str,
        filter_params: List[Any],
        limit: int,
        match_case: bool = False,
        whole_word: bool = False,
    ) -> List[SearchResultItem]:
        """Search document names without requiring a matching content segment."""
        conn = self.db.get_connection()
        conn.create_function("fold", 1, fold, deterministic=True)
        escaped_query = self._escape_like(fold(canonical_text(filename_query)))
        params: List[Any] = [f"%{escaped_query}%", *filter_params]
        rows = conn.execute(
            f"""
                SELECT id, path, filename, file_type, file_size, mtime, ctime
                FROM documents d
                WHERE fold(d.filename) LIKE ? ESCAPE '\\'
                  {filter_clause}
                ORDER BY d.filename COLLATE NOCASE, d.id
            """,
            params,
        ).fetchall()

        results = []
        for row in rows:
            if not self._literal_matches(
                row["filename"], filename_query, match_case, whole_word, stemming=False
            ):
                continue
            snippets = generate_highlighted_snippets(
                row["filename"],
                [filename_query],
                max_snippets=1,
                context_chars=80,
                case_sensitive=match_case,
                whole_word=whole_word,
                stemming=False,
            )
            results.append(
                SearchResultItem(
                    doc_id=int(row["id"]),
                    path=row["path"],
                    filename=row["filename"],
                    file_type=row["file_type"],
                    file_size=row["file_size"],
                    mtime=row["mtime"],
                    ctime=row["ctime"],
                    rank_score=-1000.0,
                    total_matches=sum(1 for _ in iter_locations(row["filename"], [filename_query], match_case=match_case, whole_word=whole_word, stemming=False)),
                    segments=[
                        SegmentMatch(
                            segment_id="",
                            segment_type="檔名",
                            snippets=snippets or [row["filename"]],
                        )
                    ],
                )
            )
        return results[:limit]

    def _search_negative_only(
        self,
        excluded_term: str,
        filter_clause: str,
        filter_params: List[Any],
        limit: int,
        match_case: bool,
        whole_word: bool,
    ) -> List[SearchResultItem]:
        """List indexed documents without a term when NOT is used alone."""
        rows = self.db.get_connection().execute(
            f"""
                SELECT d.id, d.path, d.filename, d.file_type, d.file_size,
                       d.mtime, d.ctime, s.content
                FROM documents d
                LEFT JOIN doc_segments s ON s.doc_id = d.id
                WHERE 1 = 1 {filter_clause}
                ORDER BY d.id
            """,
            filter_params,
        )
        results = []
        current_id = None
        current_row = None
        excluded = False
        for row in rows:
            if row["id"] != current_id:
                if current_row is not None and not excluded:
                    results.append(self._negative_result(current_row))
                    if len(results) >= limit:
                        return results
                current_id = row["id"]
                current_row = row
                excluded = False
            if row["content"] and self._literal_matches(
                row["content"], excluded_term, match_case, whole_word
            ):
                excluded = True
        if current_row is not None and not excluded and len(results) < limit:
            results.append(self._negative_result(current_row))
        return results

    @staticmethod
    def _negative_result(row: Any) -> SearchResultItem:
        return SearchResultItem(
            doc_id=row["id"],
            path=row["path"],
            filename=row["filename"],
            file_type=row["file_type"],
            file_size=row["file_size"],
            mtime=row["mtime"],
            ctime=row["ctime"],
            rank_score=0.0,
            total_matches=0,
            segments=[SegmentMatch("", "檔名", [html.escape(row["filename"])])],
        )

    def _search_regex(
        self,
        expression: str,
        filter_clause: str,
        filter_params: List[Any],
        limit: int,
        match_case: bool,
        whole_word: bool,
        cancel_check: Optional[Callable[[], bool]] = None,
        page_after=None, page_meta=None,
    ) -> List[SearchResultItem]:
        """Run a validated user regular expression against indexed segments, within a budget."""
        try:
            pattern = compile_user_regex(expression, match_case, whole_word)
        except RegexError as exc:
            raise SearchQueryError(f"Regex 語法錯誤：{exc}", "regex_syntax") from exc

        rows = self.db.get_connection().execute(
            f"""
                SELECT s.id AS segment_row_id, s.doc_id, s.segment_id, s.segment_type, s.content,
                       0.0 AS rank, d.path, d.filename, d.file_type,
                       d.file_size, d.mtime, d.ctime
                FROM doc_segments s
                JOIN documents d ON d.id = s.doc_id
                WHERE 1 = 1 {filter_clause}
                ORDER BY d.filename COLLATE NOCASE
            """,
            filter_params,
        )
        deadline = Deadline()

        def checked_rows():
            for row in rows:
                if cancel_check is not None and cancel_check():
                    raise SearchQueryError("搜尋已取消。", "cancelled")
                yield row

        try:
            return self._aggregate_rows(
                checked_rows(), [], limit, expression, regex_pattern=pattern, deadline=deadline,
                cancel_check=cancel_check, page_after=page_after, page_meta=page_meta
            )
        except TimeoutError as exc:
            raise SearchQueryError(
                f"正規表示式執行超過 {deadline.seconds:g} 秒，已停止；"
                "請簡化運算式（避免巢狀重複，如 (a+)+）或縮小搜尋範圍。",
                "regex_timeout",
            ) from exc

    @staticmethod
    def _index_terms(text: str) -> List[str]:
        """Words of text as the FTS index holds them: folded, cut by jieba, punctuation removed."""
        return [w for w in (t.strip() for t in jieba.cut(fold(text))) if w and has_word_char(w)]

    @staticmethod
    def _query_terms(query: str) -> List[str]:
        """The words and quoted phrases of a query, without AND/OR/NOT."""
        found = re.findall(r'"([^\"]+)"|(\bAND\b|\bOR\b|\bNOT\b)|([^\s]+)', query, re.IGNORECASE)
        return [phrase or word for phrase, operator, word in found if not operator]

    @classmethod
    def _drop_negated_literal(cls, built_parts: List[str], term: str) -> bool:
        """Leave "NOT term" out of the FTS expression when the literal check must decide.

        FTS would exclude every segment holding the term's pieces anywhere (NOT 升等 would drop
        text with 升 and 等 far apart); the literal re-check excludes only real occurrences.
        """
        if built_parts and built_parts[-1] == "NOT" and cls._term_needs_literal(term):
            built_parts.pop()
            return True
        return False

    @classmethod
    def _needs_literal_check(cls, query: str) -> bool:
        """True when FTS alone cannot judge a term, so hits must be confirmed on the stored text.

        Punctuation is not indexed, and a Chinese term jieba cannot segment into words (升等 is cut
        into 升 and 等) becomes an AND of single characters that also matches text holding those
        characters far apart.
        """
        return any(cls._term_needs_literal(term) for term in cls._query_terms(query))

    @classmethod
    def _term_needs_literal(cls, term: str) -> bool:
        if any(not (c.isalnum() or c.isspace()) for c in term):
            return True
        words = cls._index_terms(term)
        return any(
            len(a) == 1 and len(b) == 1 and _is_cjk(a) and _is_cjk(b)
            for a, b in zip(words, words[1:], strict=False)
        )

    @staticmethod
    def _literal_matches(
        content: str, term: str, match_case: bool, whole_word: bool, stemming: bool = True
    ) -> bool:
        if stemming and not (match_case or whole_word) and re.fullmatch(r"[a-zA-Z]+(?:\s+[a-zA-Z]+)+", term):
            from doc_searcher.search.matches import english_phrase_matches
            return next(english_phrase_matches(content, term), None) is not None
        if not term:
            return False
        spec = stem_spec(term) if stemming and not (match_case or whole_word) else None
        if spec is not None:  # another form of the same English word counts, as in the index
            return any(
                matches_stem(spec, found.group(0))
                for found in re.finditer(stem_regex_source(spec), content, re.IGNORECASE)
            )
        expression = script_regex(term)
        if whole_word:
            expression = rf"(?<!\w){expression}(?!\w)"
        return re.search(expression, content, 0 if match_case else re.IGNORECASE) is not None

    @classmethod
    def _matches_query_options(
        cls, content: str, query: str, match_case: bool, whole_word: bool
    ) -> bool:
        """Verify FTS candidates under case/word-boundary constraints."""
        tokens = re.findall(r'"([^\"]+)"|(\bAND\b|\bOR\b|\bNOT\b)|([^\s]+)', query, re.IGNORECASE)
        positive = []
        excluded = []
        negate_next = False
        has_or = False
        for phrase, operator, word in tokens:
            if operator:
                upper = operator.upper()
                has_or = has_or or upper == "OR"
                negate_next = upper == "NOT"
                continue
            term = phrase or word
            if not term:
                continue
            if negate_next:
                excluded.append(term)
                negate_next = False
            else:
                positive.append(term)

        if any(cls._literal_matches(content, term, match_case, whole_word) for term in excluded):
            return False
        checks = [cls._literal_matches(content, term, match_case, whole_word) for term in positive]
        return (any(checks) if has_or else all(checks)) if checks else True

    def _build_fts5_query(self, query: str) -> str:
        """Convert query string into FTS5 expression using tokenized_content column."""
        if query.count('"') % 2:
            raise SearchQueryError("精確片語的雙引號未成對。", "unpaired_phrase")
        if re.search(r"^(?:AND|OR|NOT)\b", query, re.IGNORECASE) or re.search(
            r"\b(?:AND|OR|NOT)$", query, re.IGNORECASE
        ):
            raise SearchQueryError("AND、OR、NOT 前後都必須有搜尋詞。", "operator_position")
        if re.search(
            r"\b(?:AND|OR|NOT)\s+(?:AND|OR|NOT)\b",
            query,
            re.IGNORECASE,
        ):
            raise SearchQueryError("AND、OR、NOT 不可連續使用。", "repeated_operator")

        parts = re.split(r'(\s+AND\s+|\s+OR\s+|\s+NOT\s+|".*?")', query, flags=re.IGNORECASE)

        built_parts = []
        for p in parts:
            p_strip = p.strip()
            if not p_strip:
                continue
            upper_p = p_strip.upper()
            if upper_p in {"AND", "OR", "NOT"}:
                built_parts.append(upper_p)
            elif p_strip.startswith('"') and p_strip.endswith('"') and len(p_strip) > 2:
                inner = p_strip[1:-1]
                cut_words = self._index_terms(inner)
                if self._drop_negated_literal(built_parts, inner):
                    continue
                if cut_words:
                    phrase_query = " ".join(word.replace('"', '""') for word in cut_words)
                    built_parts.append(f'tokenized_content : "{phrase_query}"')
            else:
                cut_words = self._index_terms(p_strip)
                if self._drop_negated_literal(built_parts, p_strip):
                    continue
                if cut_words:
                    sub_expr = " AND ".join(
                        f'"{word.replace(chr(34), chr(34) * 2)}"' for word in cut_words
                    )
                    built_parts.append(f"tokenized_content : ({sub_expr})")

        if not built_parts:
            return ""

        operators = {"AND", "OR", "NOT"}
        if built_parts[0].upper() in operators or built_parts[-1].upper() in operators:
            raise SearchQueryError("AND、OR、NOT 前後都必須有搜尋詞。", "operator_position")
        for previous, current in zip(built_parts, built_parts[1:], strict=False):
            if previous.upper() in operators and current.upper() in operators:
                raise SearchQueryError("AND、OR、NOT 不可連續使用。", "repeated_operator")

        final_tokens = []
        for i, part in enumerate(built_parts):
            if i > 0:
                prev = built_parts[i - 1].upper()
                curr = part.upper()
                if prev not in operators and curr not in operators:
                    final_tokens.append("AND")
            final_tokens.append(part)

        return " ".join(final_tokens)

    def _fallback_like_search(
        self,
        keywords: List[str],
        filter_clause: str,
        filter_params: List[Any],
        limit: int,
    ) -> List[Any]:
        """Fallback search using SQL LIKE if FTS query encounters syntax anomaly."""
        conn = self.db.get_connection()
        conn.create_function("fold", 1, fold, deterministic=True)
        like_clauses = " AND ".join(["fold(s.content) LIKE ? ESCAPE '\\'"] * len(keywords))
        params: List[Any] = [f"%{self._escape_like(fold(k))}%" for k in keywords]

        sql = f"""
            SELECT 
                s.doc_id,
                s.segment_id,
                s.segment_type,
                s.content,
                0.0 as rank,
                d.path,
                d.filename,
                d.file_type,
                d.file_size,
                d.mtime,
                d.ctime
            FROM doc_segments s
            JOIN documents d ON d.id = s.doc_id
            WHERE {like_clauses}
              {filter_clause}
            LIMIT ?
        """
        params.extend(filter_params)
        params.append(limit)
        cursor = conn.execute(sql, params)
        return cursor.fetchall()
