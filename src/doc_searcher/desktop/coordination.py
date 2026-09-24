# Purpose: Qt-free decisions behind the main window's indexing and search flows.
# What the code does:
#   - summarize_indexing(stats, total_docs) maps an IndexingService result to the index state,
#     the status message (i18n key + parameters), and whether "last updated" should advance.
#   - WorkScheduler serializes indexing and searching: it decides whether a request starts now
#     or waits, remembers the pending index/search, and says what to run when a worker ends.
#   - WorkScheduler.accepts_results() rejects results made stale by newer input.
# Usage notes, dependencies, or assumptions:
#   - MainWindow owns the widgets and QThread workers and passes in whether they are running;
#     this module only decides. Standard library only.

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

SearchRequest = Tuple[str, str, Dict[str, Any]]  # (query, type filter, search kwargs)


@dataclass
class IndexingOutcome:
    state: str  # index state label: idle, error, completed
    message_key: Optional[str]  # None: show the idle status text
    params: Dict[str, Any] = field(default_factory=dict)
    mark_updated: bool = False


def summarize_indexing(stats: Dict[str, Any], total_docs: Callable[[], int]) -> IndexingOutcome:
    """Decide how a finished indexing run is reported; total_docs is only queried if needed."""
    if stats.get("cancelled"):
        return IndexingOutcome("idle", "index_stopped", {"docs": total_docs()})
    if stats.get("error"):
        return IndexingOutcome("error", "index_failed", {"message": stats["error"]})

    docs = total_docs()
    counts = {key: stats.get(key, 0) for key in ("indexed", "deleted", "failed")}
    if stats.get("skipped"):
        return IndexingOutcome("completed", "index_unavailable", {"docs": docs})
    skipped = len(stats.get("unavailable_directories", [])) + len(stats.get("scan_error_paths", []))
    if skipped:
        params = {"indexed": counts["indexed"], "deleted": counts["deleted"], "skipped": skipped}
        return IndexingOutcome("completed", "index_partial", {**params, "docs": docs}, True)
    if not any(counts.values()):
        return IndexingOutcome("completed", None, mark_updated=True)
    return IndexingOutcome("completed", "index_complete", {**counts, "docs": docs}, True)


class WorkScheduler:
    """Indexing and searching never run at the same time; the later request waits."""

    def __init__(self) -> None:
        self.pending_index = False
        self.pending_search: Optional[SearchRequest] = None

    def request_index(self, index_running: bool, search_running: bool) -> str:
        """Returns "start", "queued" (behind indexing), or "queued_behind_search"."""
        if index_running:
            self.pending_index = True
            return "queued"
        if search_running:
            self.pending_index = True
            return "queued_behind_search"
        return "start"

    def request_search(
        self, request: SearchRequest, index_running: bool, search_running: bool
    ) -> str:
        """Returns "clear" (empty query), "queued", or "start"."""
        query = request[0]
        if not query:
            if search_running:
                self.pending_search = request  # makes the running search's results stale
            return "clear"
        if index_running or search_running:
            self.pending_search = request
            return "queued"
        return "start"

    def search_started(self) -> None:
        self.pending_search = None

    def after_index(self) -> Optional[str]:
        """What to run once the index worker exits: "index", "search", or None."""
        if self.pending_index:
            self.pending_index = False
            return "index"
        if self.pending_search and self.pending_search[0]:
            return "search"
        return None

    def after_search(self) -> Tuple[Optional[str], Optional[SearchRequest]]:
        """What to run once the current search worker exits, with the queued request."""
        if self.pending_index:
            self.pending_index = False
            return "index", None
        pending, self.pending_search = self.pending_search, None
        if pending and pending[0]:
            return "search", pending
        return None, None

    def accepts_results(
        self, is_current_worker: bool, finished: Tuple[Any, ...], current: Tuple[Any, ...]
    ) -> bool:
        """Results count only from the latest worker, with nothing queued, for unchanged input."""
        return is_current_worker and self.pending_search is None and finished == current
