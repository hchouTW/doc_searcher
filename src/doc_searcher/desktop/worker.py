# Purpose: Background QThread workers for non-blocking indexing and searching.
# What the code does:
#   - IndexWorker: Qt adapter over indexing.service.IndexingService (pausable/stoppable scan,
#     index, or clear); it only maps phases/results to signals.
#   - SearchWorker: Executes filtered full-text queries asynchronously, preventing UI lockups.
# Usage notes, dependencies, or assumptions:
#   - Requires PySide6.QtCore (QThread, Signal).

import time
from typing import Any, Dict, List, Optional, Tuple
from PySide6.QtCore import QThread, Signal

from doc_searcher.storage.database import Database
from doc_searcher.indexing.service import IndexingService, IndexRequest
from doc_searcher.search.searcher import DocumentSearcher


class IndexWorker(QThread):
    """Qt adapter that runs IndexingService on a background thread and reports via signals."""

    # Signals: current_count, total_count, current_filename
    progress = Signal(int, int, str)
    status_changed = Signal(str)
    indexing_finished = Signal(dict)
    state_changed = Signal(str)

    _STATES = {
        "scanning": "scanning",
        "indexing": "indexing",
        "completed": "completed",
        "cancelled": "idle",
    }
    _MESSAGES = {
        "scanning": "正在掃描資料夾檔案...",
        "indexing": "正在更新索引...",
        "cancelled": "正在停止索引...",
    }

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
        self.request = IndexRequest(
            roots=list(directories),
            include_subdirectories=include_subdirectories,
            exclude_patterns=list(exclude_patterns or []),
        )
        self.clear_index = clear_index
        self.service = IndexingService(db)
        self._active_state = "scanning"

    def run(self):
        try:
            self._run_indexing()
        except Exception as exc:
            message = str(exc)
            self.state_changed.emit("error")
            self.status_changed.emit(f"索引失敗：{message}")
            self.indexing_finished.emit({"indexed": 0, "deleted": 0, "failed": 1, "error": message})
        finally:
            self.db.close()

    def _run_indexing(self):
        if self.clear_index:
            self.state_changed.emit("indexing")
            stats = self.service.clear(on_progress=self.progress.emit)
            self.state_changed.emit("idle" if stats["cancelled"] else "completed")
            self.indexing_finished.emit(stats)
            return
        stats = self.service.run(
            self.request, on_progress=self.progress.emit, on_phase=self._on_phase
        )
        if stats.get("skipped") and not stats.get("cancelled"):
            self.status_changed.emit("所有檢索目錄目前皆無法存取，已保留既有索引。")
        elif not stats.get("cancelled"):
            self.status_changed.emit(
                f"索引完成：成功 {stats['indexed']} 個，刪除 {stats['deleted']} 個，"
                f"費時 {stats['elapsed']:.2f} 秒。"
            )
        self.indexing_finished.emit(stats)

    def _on_phase(self, phase: str):
        state = self._STATES[phase]
        if phase in ("scanning", "indexing"):
            self._active_state = state
        self.state_changed.emit(state)
        if phase in self._MESSAGES:
            self.status_changed.emit(self._MESSAGES[phase])

    def cancel(self):
        self.service.cancel()

    def pause(self):
        self.service.pause()
        self.state_changed.emit("paused")
        self.status_changed.emit("索引已暫停；按「恢復索引」繼續。")

    def resume(self):
        self.service.resume()
        self.state_changed.emit(self._active_state)
        self.status_changed.emit("正在恢復掃描索引...")

    @property
    def is_paused(self) -> bool:
        return self.service.is_paused


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
