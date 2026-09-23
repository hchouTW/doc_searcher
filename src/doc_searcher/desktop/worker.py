# Purpose: Background QThread workers for non-blocking indexing and searching.
# What the code does:
#   - IndexWorker: Runs pausable/stoppable scanning, indexing, or index clearing in background.
#   - SearchWorker: Executes filtered full-text queries asynchronously, preventing UI lockups.
# Usage notes, dependencies, or assumptions:
#   - Requires PySide6.QtCore (QThread, Signal).

import time
from typing import Any, Dict, List, Optional, Tuple
from PySide6.QtCore import QThread, Signal

from doc_searcher.storage.database import Database
from doc_searcher.indexing.scanner import FileScanner
from doc_searcher.indexing.indexer import DocumentIndexer
from doc_searcher.search.searcher import DocumentSearcher


class IndexWorker(QThread):
    """Background worker thread for indexing files without blocking UI."""

    # Signals: current_count, total_count, current_filename
    progress = Signal(int, int, str)
    status_changed = Signal(str)
    indexing_finished = Signal(dict)
    state_changed = Signal(str)

    def __init__(
        self,
        db: Database,
        directories: List[str],
        include_subdirectories: bool = True,
        exclude_patterns: Optional[List[str]] = None,
        clear_index: bool = False,
    ):
        super().__init__()
        self.db = db
        self.directories = directories
        self.include_subdirectories = include_subdirectories
        self.exclude_patterns = exclude_patterns or []
        self.clear_index = clear_index
        self.indexer = DocumentIndexer(db)
        self.scanner = FileScanner()
        self._active_state = "scanning"

    def run(self):
        try:
            self._run_indexing()
        except Exception as exc:
            message = str(exc)
            self.state_changed.emit("error")
            self.status_changed.emit(f"索引失敗：{message}")
            self.indexing_finished.emit(
                {
                    "indexed": 0,
                    "deleted": 0,
                    "failed": 1,
                    "error": message,
                }
            )
        finally:
            self.db.close()

    def _run_indexing(self):
        if self.clear_index:
            self.state_changed.emit("indexing")
            paths = list(self.db.get_all_indexed_paths())
            stats = self.indexer.run_batch_indexing(
                [],
                paths,
                progress_callback=lambda current, total, path: self.progress.emit(
                    current, total, path
                ),
                reset_cancellation=False,
            )
            self.state_changed.emit("idle" if stats["cancelled"] else "completed")
            self.indexing_finished.emit(stats)
            return
        self.state_changed.emit("scanning")
        self._active_state = "scanning"
        self.status_changed.emit("正在掃描資料夾檔案...")
        start_time = time.time()

        available_directories, unavailable_directories = self.scanner.partition_directories(
            self.directories
        )
        if not available_directories:
            indexed_files = self.db.get_all_indexed_paths()
            _, to_delete = self.scanner.calculate_changes(
                [],
                indexed_files,
                preserved_directories=unavailable_directories,
            )
            if to_delete:
                stats = self.indexer.run_batch_indexing(
                    [],
                    to_delete,
                    progress_callback=lambda current, total, path: self.progress.emit(
                        current, total, path
                    ),
                    reset_cancellation=False,
                )
                if stats["cancelled"]:
                    stats["unavailable_directories"] = unavailable_directories
                    self.indexing_finished.emit(stats)
                    return
            self.status_changed.emit("所有檢索目錄目前皆無法存取，已保留既有索引。")
            self.indexing_finished.emit(
                {
                    "indexed": 0,
                    "deleted": len(to_delete),
                    "failed": 0,
                    "skipped": True,
                    "unavailable_directories": unavailable_directories,
                    "elapsed": time.time() - start_time,
                }
            )
            return

        # 1. Scan filesystem
        scan_error_paths = []
        current_files = self.scanner.scan_directories(
            available_directories,
            self.include_subdirectories,
            checkpoint=self.indexer.wait_until_ready,
            error_callback=scan_error_paths.append,
            exclude_patterns=self.exclude_patterns,
        )
        scan_error_paths = self.scanner.normalize_directories(scan_error_paths)

        if self.indexer.is_cancelled:
            self.indexing_finished.emit(
                {
                    "indexed": 0,
                    "deleted": 0,
                    "failed": 0,
                    "cancelled": True,
                    "elapsed": time.time() - start_time,
                }
            )
            return

        indexed_files = self.db.get_all_indexed_paths()

        # 2. Compute incremental diff
        to_index, to_delete = self.scanner.calculate_changes(
            current_files,
            indexed_files,
            preserved_directories=[*unavailable_directories, *scan_error_paths],
        )

        total_tasks = len(to_index) + len(to_delete)
        if total_tasks == 0:
            self.state_changed.emit("completed")
            self.status_changed.emit("所有文件索引皆為最新狀態。")
            self.indexing_finished.emit(
                {
                    "indexed": 0,
                    "deleted": 0,
                    "failed": 0,
                    "unavailable_directories": unavailable_directories,
                    "scan_error_paths": scan_error_paths,
                    "elapsed": time.time() - start_time,
                }
            )
            return

        self.state_changed.emit("indexing")
        self._active_state = "indexing"
        self.status_changed.emit(
            f"準備更新索引 (新增/變更: {len(to_index)} 個，刪除: {len(to_delete)} 個)..."
        )

        def _on_progress(curr, tot, fname):
            self.progress.emit(curr, tot, fname)

        stats = self.indexer.run_batch_indexing(
            to_index,
            to_delete,
            progress_callback=_on_progress,
            reset_cancellation=False,
        )
        stats["elapsed"] = time.time() - start_time
        stats["unavailable_directories"] = unavailable_directories
        stats["scan_error_paths"] = scan_error_paths
        if stats.get("cancelled"):
            self.state_changed.emit("idle")
            self.status_changed.emit("正在停止索引...")
        else:
            self.state_changed.emit("completed")
            self.status_changed.emit(
                f"索引完成：成功 {stats['indexed']} 個，刪除 {stats['deleted']} 個，"
                f"費時 {stats['elapsed']:.2f} 秒。"
            )
        self.indexing_finished.emit(stats)

    def cancel(self):
        self.indexer.cancel()

    def pause(self):
        self.indexer.pause()
        self.state_changed.emit("paused")
        self.status_changed.emit("索引已暫停；按「恢復索引」繼續。")

    def resume(self):
        self.indexer.resume()
        self.state_changed.emit(self._active_state)
        self.status_changed.emit("正在恢復掃描索引...")

    @property
    def is_paused(self) -> bool:
        return self.indexer.is_paused


class SearchWorker(QThread):
    """Background worker for asynchronous full-text search."""

    search_finished = Signal(list, str, float)  # results, query, elapsed_ms
    search_failed = Signal(str)

    def __init__(
        self,
        db: Database,
        query: str,
        type_filter: str = "all",
        search_filters: Optional[Dict[str, Any]] = None,
    ):
        super().__init__()
        self.db = db
        self.query = query
        self.type_filter = type_filter
        self.search_filters = search_filters or {}
        self.searcher = DocumentSearcher(db)
        # Set by MainWindow to detect results made stale by a filter change.
        self.filter_signature: Tuple[Any, ...] = ()

    def run(self):
        try:
            start = time.perf_counter()
            results = self.searcher.search(
                self.query,
                type_filter=self.type_filter,
                **self.search_filters,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self.search_finished.emit(results, self.query, elapsed_ms)
        except Exception as exc:
            self.search_failed.emit(str(exc))
        finally:
            self.db.close()
