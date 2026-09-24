# Purpose: The single implementation of incremental indexing used by the CLI, desktop, and MCP.
# What the code does:
#   - IndexingService.run(IndexRequest) normalizes roots, splits available from unavailable
#     roots, scans, reconciles against the index, and parses/deletes documents, reporting
#     progress and phase changes through plain callbacks.
#   - Reconciliation scope: with request.scope None the roots are the complete configured set,
#     so index entries outside them (removed roots) are pruned; with a scope list only entries
#     inside those directories are reconciled. Unavailable roots and unreadable subfolders are
#     always preserved, never treated as deleted.
#   - pause()/resume()/cancel() are cooperative and safe to call from another thread.
#   - clear() removes every indexed document.
#   - Holds a per-database, in-process lock while writing, so two runs never write concurrently.
# Usage notes, dependencies, or assumptions:
#   - No Qt, MCP, or printing here; adapters translate phases/results into signals or messages.
#   - Result keys: indexed, deleted, failed, cancelled, skipped (no root was available),
#     scanned, unavailable_directories, scan_error_paths, elapsed.

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from doc_searcher.indexing.indexer import DocumentIndexer
from doc_searcher.indexing.scanner import FileScanner
from doc_searcher.storage.database import Database

ProgressCallback = Callable[[int, int, str], None]
PhaseCallback = Callable[[str], None]  # "scanning", "indexing", "completed", "cancelled"

_WRITE_LOCKS: Dict[str, threading.Lock] = {}
_WRITE_LOCKS_GUARD = threading.Lock()


def _write_lock(db_path: str) -> threading.Lock:
    with _WRITE_LOCKS_GUARD:
        return _WRITE_LOCKS.setdefault(db_path, threading.Lock())


@dataclass
class IndexRequest:
    roots: List[str]
    include_subdirectories: bool = True
    exclude_patterns: List[str] = field(default_factory=list)
    scope: Optional[List[str]] = None


class IndexingService:
    """Scan, reconcile, and index one request at a time against one database."""

    def __init__(self, db: Database, scanner: Optional[FileScanner] = None):
        self.db = db
        self.scanner = scanner or FileScanner()
        self.indexer = DocumentIndexer(db)

    # ------------------------------------------------------------ controls
    def pause(self) -> None:
        self.indexer.pause()

    def resume(self) -> None:
        self.indexer.resume()

    def cancel(self) -> None:
        self.indexer.cancel()

    @property
    def is_paused(self) -> bool:
        return self.indexer.is_paused

    @property
    def is_cancelled(self) -> bool:
        return self.indexer.is_cancelled

    # ---------------------------------------------------------------- runs
    def run(
        self,
        request: IndexRequest,
        on_progress: Optional[ProgressCallback] = None,
        on_phase: Optional[PhaseCallback] = None,
    ) -> Dict[str, Any]:
        """Bring the index in line with the requested roots; returns result counts."""
        phase = on_phase or (lambda _name: None)
        start = time.time()
        with self._locked():
            if self.is_cancelled:
                return self._finish(phase, self._cancelled_result(start))
            phase("scanning")
            available, unavailable = self.scanner.partition_directories(request.roots)
            scan_errors: List[str] = []
            current_files = self.scanner.scan_directories(
                available,
                request.include_subdirectories,
                checkpoint=self.indexer.wait_until_ready,
                error_callback=scan_errors.append,
                exclude_patterns=request.exclude_patterns,
            )
            scan_errors = self.scanner.normalize_directories(scan_errors)
            if self.is_cancelled:
                return self._finish(phase, self._cancelled_result(start))

            to_index, to_delete = self.scanner.calculate_changes(
                current_files,
                self._indexed_in_scope(request.scope),
                preserved_directories=[*unavailable, *scan_errors],
            )
            if to_index or to_delete:
                phase("indexing")
            stats: Dict[str, Any] = self.indexer.run_batch_indexing(
                to_index, to_delete, progress_callback=on_progress, reset_cancellation=False
            )
            stats.update(
                skipped=not available,
                scanned=len(current_files),
                unavailable_directories=unavailable,
                scan_error_paths=scan_errors,
                elapsed=time.time() - start,
            )
            return self._finish(phase, stats)

    def clear(self, on_progress: Optional[ProgressCallback] = None) -> Dict[str, Any]:
        """Remove every document from the index."""
        start = time.time()
        with self._locked():
            paths = list(self.db.get_all_indexed_paths())
            stats: Dict[str, Any] = self.indexer.run_batch_indexing(
                [], paths, progress_callback=on_progress, reset_cancellation=False
            )
        stats["elapsed"] = time.time() - start
        return stats

    # ------------------------------------------------------------- helpers
    def _locked(self):
        return _write_lock(self.db.db_path)

    def _indexed_in_scope(self, scope: Optional[List[str]]):
        indexed = self.db.get_all_indexed_paths()
        if scope is None:
            return indexed
        return {
            path: value
            for path, value in indexed.items()
            if any(self.scanner.is_path_within_directory(path, root) for root in scope)
        }

    @staticmethod
    def _cancelled_result(start: float) -> Dict[str, Any]:
        return {
            "indexed": 0,
            "deleted": 0,
            "failed": 0,
            "cancelled": True,
            "elapsed": time.time() - start,
        }

    @staticmethod
    def _finish(phase: PhaseCallback, stats: Dict[str, Any]) -> Dict[str, Any]:
        phase("cancelled" if stats.get("cancelled") else "completed")
        return stats
