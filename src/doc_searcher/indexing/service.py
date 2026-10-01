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
#   - clear() atomically resets documents and search indexes in one transaction; pause/cancel
#     apply before the reset, and progress reports its start and committed completion.
#   - Holds a per-database, in-process lock while writing, so two runs never write concurrently.
# Usage notes, dependencies, or assumptions:
#   - No Qt, MCP, or printing here; adapters translate phases/results into signals or messages.
#   - Result keys: indexed, deleted, failed, cancelled, skipped (no root was available),
#     scanned, unavailable_directories, scan_error_paths, elapsed.

import os
import stat
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from doc_searcher.indexing.indexer import DocumentIndexer
from doc_searcher.indexing.scanner import FileScanner
from doc_searcher.storage.database import Database
from doc_searcher.platform.paths import canonical_path

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
    force_paths: List[str] = field(default_factory=list)
    reprocess_only: bool = False


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
            force = {canonical_path(path) for path in request.force_paths}
            permitted = [
                path
                for path, _, _ in current_files
                if path in force
                and (
                    request.scope is None
                    or any(
                        self.scanner.is_path_within_directory(path, root) for root in request.scope
                    )
                )
            ]
            if request.reprocess_only:
                to_index, to_delete = permitted, []
            else:
                to_index = list(dict.fromkeys([*to_index, *permitted]))
            if to_index or to_delete:
                phase("indexing")
            stats: Dict[str, Any] = self.indexer.run_batch_indexing(
                to_index, to_delete, progress_callback=on_progress, reset_cancellation=False
            )
            stats.update(
                skipped=not available,
                scanned=len(current_files),
                reprocess_skipped=sorted(force - set(permitted)),
                unavailable_directories=unavailable,
                scan_error_paths=scan_errors,
                elapsed=time.time() - start,
            )
            return self._finish(phase, stats)

    def update_paths(self, request: IndexRequest, paths: List[str]) -> Dict[str, Any]:
        """Update affected files only; inaccessible roots/files are never deletions."""
        start = time.monotonic()
        with self._locked():
            available, unavailable = self.scanner.partition_directories(request.roots)
            indexed = self._indexed_in_scope(request.scope)
            updates, deletes, errors = [], [], []
            for raw_path in dict.fromkeys(paths):
                path = canonical_path(raw_path)
                roots = [
                    root for root in available if self.scanner.is_path_within_directory(path, root)
                ]
                if request.scope is not None and not any(
                    self.scanner.is_path_within_directory(path, root) for root in request.scope
                ):
                    continue
                if not roots or not self.scanner.is_valid_document_file(os.path.basename(path)):
                    continue
                roots = [
                    root
                    for root in roots
                    if (request.include_subdirectories or os.path.dirname(path) == root)
                    and not any(
                        part.startswith(".") and part != "."
                        for part in os.path.relpath(os.path.dirname(path), root).split(os.sep)
                    )
                    and not self.scanner.matches_exclusion(path, request.exclude_patterns, root)
                ]
                if not roots:
                    continue
                try:
                    metadata = os.stat(path)
                except FileNotFoundError:
                    if path in indexed:
                        deletes.append(path)
                    continue
                except OSError as exc:
                    errors.append(dict(path=path, message=str(exc)))
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                # Events force parsing: same-size writes or preserved timestamps still matter.
                updates.append(path)
            result = self.indexer.run_batch_indexing(updates, deletes, reset_cancellation=False)
            result.update(
                elapsed=time.monotonic() - start,
                unavailable_directories=unavailable,
                scan_errors=errors,
            )
            return result

    def clear(self, on_progress: Optional[ProgressCallback] = None) -> Dict[str, Any]:
        """Clear the index in bulk; cancellation before the transaction preserves all text."""
        start = time.time()
        with self._locked():
            if not self.indexer.wait_until_ready():
                return self._cancelled_result(start)
            total = self.db.get_connection().execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            if on_progress:
                on_progress(0, total, "")
            if not self.indexer.wait_until_ready():
                return self._cancelled_result(start)
            deleted = self.db.clear_index()
            if on_progress:
                on_progress(deleted, deleted, "")
        return {
            "indexed": 0,
            "deleted": deleted,
            "failed": 0,
            "cancelled": False,
            "elapsed": time.time() - start,
        }

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
