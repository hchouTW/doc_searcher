# Purpose: Full-text search engine, query builder, and result ranker.
# What the code does:
#   - Translates natural queries, boolean expressions, and quoted phrases into FTS5 match syntax.
#   - Supports explicit filename searches and user-facing query validation.
#   - Applies file type, mtime/ctime, size, include-path, and exclusion filters safely.
#     With search_roots, relative exclusion patterns only apply below the search folders.
#   - Supports case-sensitive, whole-word, and validated regular-expression modes.
#   - Executes FTS queries against tokenized and raw content with BM25 ranking.
#   - Generates snippet contexts with highlighted HTML <mark> tags and location indicators.
# Usage notes, dependencies, or assumptions:
#   - Uses SQLite FTS5 functions and doc_searcher.search.text_helper.

import os
import html
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import jieba

from doc_searcher.storage.database import Database
from doc_searcher.indexing.scanner import is_absolute_pattern
from doc_searcher.search.text_helper import (
    extract_keywords_from_query,
    generate_highlighted_snippets,
    generate_regex_highlighted_snippets,
)


@dataclass
class SegmentMatch:
    segment_id: str
    segment_type: str
    snippets: List[str]


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


class SearchQueryError(ValueError):
    """Raised when a user query has invalid boolean or quote syntax."""


class DocumentSearcher:
    """Executes full-text queries and formats ranked search results."""

    def __init__(self, db: Database):
        self.db = db

    def search(
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
    ) -> List[SearchResultItem]:
        """Search documents matching query string and optional metadata filters."""
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
                clean_query, filter_clause, filter_params, limit, match_case, whole_word
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

        keywords = extract_keywords_from_query(clean_query)
        if not keywords:
            return []

        fts_query = self._build_fts5_query(clean_query)
        if not fts_query:
            return []

        conn = self.db.get_connection()

        params: List[Any] = [fts_query]

        sql = f"""
            SELECT 
                f.doc_id,
                f.segment_id,
                f.segment_type,
                f.content,
                bm25(doc_fts) as rank,
                d.path,
                d.filename,
                d.file_type,
                d.file_size,
                d.mtime,
                d.ctime
            FROM doc_fts f
            JOIN documents d ON d.id = CAST(f.doc_id AS INTEGER)
            WHERE doc_fts MATCH ?
              {filter_clause}
            ORDER BY rank ASC
            LIMIT ?
        """
        params.extend(filter_params)
        params.append(limit * 3)

        try:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
        except Exception as e:
            print(f"[Searcher] FTS search error: {e}, falling back to LIKE query")
            rows = self._fallback_like_search(keywords, filter_clause, filter_params, limit)

        return self._aggregate_rows(rows, keywords, limit, clean_query, match_case, whole_word)

    def _aggregate_rows(
        self,
        rows: List[Any],
        keywords: List[str],
        limit: int,
        query: str,
        match_case: bool = False,
        whole_word: bool = False,
        regex_pattern: Optional[re.Pattern] = None,
    ) -> List[SearchResultItem]:
        """Aggregate matching segment rows into ranked document results."""
        doc_map: Dict[int, SearchResultItem] = {}
        for row in rows:
            doc_id = int(row["doc_id"])
            seg_id = str(row["segment_id"])
            seg_type = str(row["segment_type"])
            content = row["content"]
            rank = float(row["rank"])

            if regex_pattern is None and (match_case or whole_word):
                if not self._matches_query_options(content, query, match_case, whole_word):
                    continue

            if regex_pattern is not None:
                snippets = generate_regex_highlighted_snippets(
                    content, regex_pattern, max_snippets=3, context_chars=60
                )
            else:
                snippets = generate_highlighted_snippets(
                    content,
                    keywords,
                    max_snippets=3,
                    context_chars=60,
                    case_sensitive=match_case,
                    whole_word=whole_word,
                )
            if not snippets:
                continue

            if doc_id not in doc_map:
                doc_map[doc_id] = SearchResultItem(
                    doc_id=doc_id,
                    path=row["path"],
                    filename=row["filename"],
                    file_type=row["file_type"],
                    file_size=row["file_size"],
                    mtime=row["mtime"],
                    ctime=row["ctime"],
                    rank_score=rank,
                    total_matches=len(snippets),
                    segments=[
                        SegmentMatch(segment_id=seg_id, segment_type=seg_type, snippets=snippets)
                    ],
                )
            else:
                doc_map[doc_id].total_matches += len(snippets)
                # Keep the best (lowest) rank score
                if rank < doc_map[doc_id].rank_score:
                    doc_map[doc_id].rank_score = rank
                doc_map[doc_id].segments.append(
                    SegmentMatch(segment_id=seg_id, segment_type=seg_type, snippets=snippets)
                )

        results = list(doc_map.values())
        results.sort(key=lambda item: item.rank_score)
        return results[:limit]

    @staticmethod
    def _build_type_clause(type_filter: str) -> str:
        """Build the fixed SQL clause for a supported file-type filter."""
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

        normalized_paths = [os.path.abspath(path) for path in (include_paths or []) if path]
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
            {os.path.abspath(root).rstrip("/\\") for root in (search_roots or []) if root},
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
            pattern = raw_pattern.strip().replace("\\", "/")
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
            raise SearchQueryError("請在 filename: 或 檔名: 後輸入檔名關鍵字。")
        if filename_query.startswith('"') or filename_query.endswith('"'):
            if not (
                len(filename_query) >= 2
                and filename_query.startswith('"')
                and filename_query.endswith('"')
            ):
                raise SearchQueryError("檔名搜尋的雙引號未成對。")
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
        escaped_query = filename_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params: List[Any] = [f"%{escaped_query}%", *filter_params, limit]
        rows = conn.execute(
            f"""
                SELECT id, path, filename, file_type, file_size, mtime, ctime
                FROM documents d
                WHERE d.filename LIKE ? ESCAPE '\\'
                  {filter_clause}
                ORDER BY d.filename COLLATE NOCASE
                LIMIT ?
            """,
            params,
        ).fetchall()

        results = []
        for row in rows:
            if not self._literal_matches(row["filename"], filename_query, match_case, whole_word):
                continue
            snippets = generate_highlighted_snippets(
                row["filename"],
                [filename_query],
                max_snippets=1,
                context_chars=80,
                case_sensitive=match_case,
                whole_word=whole_word,
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
                    total_matches=1,
                    segments=[
                        SegmentMatch(
                            segment_id="",
                            segment_type="檔名",
                            snippets=snippets or [row["filename"]],
                        )
                    ],
                )
            )
        return results

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
    ) -> List[SearchResultItem]:
        """Run a validated Python regular expression against indexed segments."""
        try:
            if whole_word:
                expression = rf"(?<!\w)(?:{expression})(?!\w)"
            pattern = re.compile(expression, 0 if match_case else re.IGNORECASE)
        except re.error as exc:
            raise SearchQueryError(f"Regex 語法錯誤：{exc}") from exc

        rows = self.db.get_connection().execute(
            f"""
                SELECT s.doc_id, s.segment_id, s.segment_type, s.content,
                       0.0 AS rank, d.path, d.filename, d.file_type,
                       d.file_size, d.mtime, d.ctime
                FROM doc_segments s
                JOIN documents d ON d.id = s.doc_id
                WHERE 1 = 1 {filter_clause}
                ORDER BY d.filename COLLATE NOCASE
            """,
            filter_params,
        )
        return self._aggregate_rows(rows, [], limit, expression, regex_pattern=pattern)

    @staticmethod
    def _literal_matches(content: str, term: str, match_case: bool, whole_word: bool) -> bool:
        expression = re.escape(term)
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
            raise SearchQueryError("精確片語的雙引號未成對。")
        if re.search(r"^(?:AND|OR|NOT)\b", query, re.IGNORECASE) or re.search(
            r"\b(?:AND|OR|NOT)$", query, re.IGNORECASE
        ):
            raise SearchQueryError("AND、OR、NOT 前後都必須有搜尋詞。")
        if re.search(
            r"\b(?:AND|OR|NOT)\s+(?:AND|OR|NOT)\b",
            query,
            re.IGNORECASE,
        ):
            raise SearchQueryError("AND、OR、NOT 不可連續使用。")

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
                cut_words = [w.strip() for w in jieba.cut(inner) if w.strip()]
                if cut_words:
                    phrase_query = " ".join(word.replace('"', '""') for word in cut_words)
                    built_parts.append(f'tokenized_content : "{phrase_query}"')
            else:
                cut_words = [w.strip() for w in jieba.cut(p_strip) if w.strip()]
                if cut_words:
                    sub_expr = " AND ".join(
                        f'"{word.replace(chr(34), chr(34) * 2)}"' for word in cut_words
                    )
                    built_parts.append(f"tokenized_content : ({sub_expr})")

        if not built_parts:
            return ""

        operators = {"AND", "OR", "NOT"}
        if built_parts[0].upper() in operators or built_parts[-1].upper() in operators:
            raise SearchQueryError("AND、OR、NOT 前後都必須有搜尋詞。")
        for previous, current in zip(built_parts, built_parts[1:]):
            if previous.upper() in operators and current.upper() in operators:
                raise SearchQueryError("AND、OR、NOT 不可連續使用。")

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
        like_clauses = " AND ".join(["s.content LIKE ?"] * len(keywords))
        params: List[Any] = [f"%{k}%" for k in keywords]

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
