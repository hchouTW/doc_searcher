# Purpose: Headless, UI-independent facade over the search and indexing core for integrations
#          such as the MCP server (doc_searcher.integrations.mcp_server).
# What the code does:
#   - Runs searches across one or more format groups and returns plain, JSON-ready dicts with
#     Markdown-bold snippets instead of the GUI's HTML <mark> markup.
#   - Reports index statistics, configured directories, and background re-index state.
#   - Runs one incremental re-index at a time on a background thread, limited to the
#     directories configured in the desktop app (or a subfolder of one).
#   - Returns the stored text of an indexed document; never reads files outside the index.
# Usage notes, dependencies, or assumptions:
#   - Shares AppConfig / index.db with the GUI (~/.doc_searcher, or DOC_SEARCHER_DATA_DIR).
#   - Raises ValueError for invalid caller input and LookupError for unknown documents.
#   - No Qt or MCP imports; keeps the core package's Python 3.8 compatibility.

import html
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from doc_searcher.config import AppConfig
from doc_searcher.storage.database import Database
from doc_searcher.indexing.indexer import DocumentIndexer
from doc_searcher.indexing.scanner import FileScanner
from doc_searcher.search.searcher import DocumentSearcher, SearchQueryError, SearchResultItem
from doc_searcher.version import APP_VERSION

FORMAT_GROUPS = ("pdf", "word", "excel", "ppt", "text")
MAX_SEARCH_LIMIT = 100
DOCUMENT_URI_PREFIX = "docsearcher://document/"

_MARK_RE = re.compile(r"<mark[^>]*>(.*?)</mark>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def document_uri(path: str) -> str:
    """Build the MCP resource URI for an indexed document path."""
    return DOCUMENT_URI_PREFIX + quote(path, safe="")


def _plain_snippet(snippet_html: str) -> str:
    """Convert a highlighted HTML snippet to plain text with **bold** hits."""
    text = _MARK_RE.sub(r"**\1**", snippet_html)
    return html.unescape(_TAG_RE.sub("", text))


def _iso_time(timestamp: Optional[float]) -> Optional[str]:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


class SearchService:
    """Search, status, re-index, and document-text operations without any UI."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig()
        self.db = Database(self.config.db_path)
        self.searcher = DocumentSearcher(self.db)
        self.scanner = FileScanner()
        self._reindex_lock = threading.Lock()
        self._reindex_thread: Optional[threading.Thread] = None
        self._reindex_state: Dict[str, Any] = {"state": "idle"}

    # ----------------------------------------------------------------- search
    def search(
        self,
        query: str,
        formats: Optional[List[str]] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Search the index; formats limits results to format groups (all when empty)."""
        if not query or not query.strip():
            raise ValueError("Query must not be empty.")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}.")
        groups = [fmt.lower() for fmt in (formats or [])]
        unknown = sorted(set(groups) - set(FORMAT_GROUPS))
        if unknown:
            raise ValueError(
                f"Unknown format(s): {', '.join(unknown)}. Use: {', '.join(FORMAT_GROUPS)}."
            )

        try:
            if groups:
                items: List[SearchResultItem] = []
                for group in dict.fromkeys(groups):
                    items.extend(self.searcher.search(query, type_filter=group, limit=limit))
                # Lower rank_score is better in every search mode.
                items.sort(key=lambda item: item.rank_score)
            else:
                items = self.searcher.search(query, limit=limit)
        except SearchQueryError as exc:
            raise ValueError(str(exc)) from exc

        results = [self._result_to_dict(item) for item in items[:limit]]
        return {"query": query, "result_count": len(results), "results": results}

    @staticmethod
    def _result_to_dict(item: SearchResultItem) -> Dict[str, Any]:
        return {
            "path": item.path,
            "filename": item.filename,
            "file_type": item.file_type,
            "size_bytes": item.file_size,
            "modified": _iso_time(item.mtime),
            "match_count": item.total_matches,
            "matches": [
                {
                    "location_type": segment.segment_type,
                    "location": segment.segment_id,
                    "snippets": [_plain_snippet(snippet) for snippet in segment.snippets],
                }
                for segment in item.segments
            ],
            "resource_uri": document_uri(item.path),
        }

    # ----------------------------------------------------------------- status
    def index_status(self) -> Dict[str, Any]:
        """Return index statistics, configured directories, and re-index state."""
        self.config.load()
        stats = self.db.get_stats()
        return {
            "app_version": APP_VERSION,
            "index_path": self.db.db_path,
            "total_documents": stats["total_docs"],
            "total_size_bytes": stats["total_size"],
            "total_segments": stats["total_segments"],
            "last_indexed_at": _iso_time(stats["last_indexed_at"]),
            "directories": list(self.config.directories),
            "reindex": dict(self._reindex_state),
        }

    # ---------------------------------------------------------------- reindex
    def start_reindex(self, directory: Optional[str] = None) -> Dict[str, Any]:
        """Start a background incremental re-index; returns immediately.

        Without directory, all configured directories are rescanned. A directory must be one
        of the configured directories or inside one; only that subtree is rescanned.
        """
        self.config.load()
        configured = list(self.config.directories)
        if not configured:
            raise ValueError(
                "No search folders are configured. Add folders in the DocSearcher app first."
            )

        scope: Optional[str] = None
        if directory:
            scope = os.path.abspath(os.path.expanduser(directory))
            if not any(self.scanner.is_path_within_directory(scope, root) for root in configured):
                raise ValueError(
                    f"{scope} is not inside a configured search folder. "
                    f"Configured folders: {', '.join(configured)}"
                )
            targets = [scope]
        else:
            targets = configured

        with self._reindex_lock:
            if self._reindex_thread is not None and self._reindex_thread.is_alive():
                return {"status": "already_running", "reindex": dict(self._reindex_state)}
            self._reindex_state = {
                "state": "running",
                "directories": targets,
                "started_at": _iso_time(time.time()),
            }
            self._reindex_thread = threading.Thread(
                target=self._run_reindex, args=(targets, scope), name="mcp-reindex", daemon=True
            )
            self._reindex_thread.start()
        return {"status": "started", "reindex": dict(self._reindex_state)}

    def wait_for_reindex(self, timeout: Optional[float] = None) -> None:
        """Block until the current background re-index finishes (used by tests)."""
        thread = self._reindex_thread
        if thread is not None:
            thread.join(timeout)

    def _run_reindex(self, targets: List[str], scope: Optional[str]) -> None:
        try:
            result = self._reindex(targets, scope)
            state = "completed"
        except Exception as exc:  # Reported through index_status instead of crashing silently.
            result = {"error": str(exc)}
            state = "failed"
        finally:
            self.db.close()  # Closes only this worker thread's connection.
        self._reindex_state = {
            **self._reindex_state,
            "state": state,
            "finished_at": _iso_time(time.time()),
            "result": result,
        }

    def _reindex(self, targets: List[str], scope: Optional[str]) -> Dict[str, Any]:
        available, unavailable = self.scanner.partition_directories(targets)
        if not available:
            return {"skipped": True, "unavailable_directories": unavailable}

        scan_errors: List[str] = []
        current_files = self.scanner.scan_directories(
            available,
            self.config.include_subdirectories,
            error_callback=scan_errors.append,
            exclude_patterns=self.config.exclude_patterns,
        )
        indexed = self.db.get_all_indexed_paths()
        if scope is not None:
            # Only reconcile the requested subtree so other folders keep their entries.
            indexed = {
                path: value for path, value in indexed.items()
                if self.scanner.is_path_within_directory(path, scope)
            }
        to_index, to_delete = self.scanner.calculate_changes(
            current_files,
            indexed,
            preserved_directories=[*unavailable, *self.scanner.normalize_directories(scan_errors)],
        )
        stats: Dict[str, Any] = DocumentIndexer(self.db).run_batch_indexing(to_index, to_delete)
        stats["scanned"] = len(current_files)
        if unavailable:
            stats["unavailable_directories"] = unavailable
        return stats

    # --------------------------------------------------------------- document
    def document_text(self, path: str) -> str:
        """Return an indexed document's stored text; raises LookupError if not indexed."""
        doc = self.db.get_document_by_path(path)
        if doc is None:
            raise LookupError(f"Document is not in the index: {path}")
        if doc["error"]:
            raise LookupError(f"Document could not be parsed when indexed: {doc['error']}")

        lines = [
            f"# {doc['filename']}",
            "",
            f"- Path: {doc['path']}",
            f"- Type: {doc['file_type']}",
            f"- Modified: {_iso_time(doc['mtime'])}",
        ]
        for segment in self.db.get_document_segments(doc["id"]):
            lines += ["", f"## {segment['segment_type']} {segment['segment_id']}", "", segment["content"]]
        return "\n".join(lines) + "\n"
