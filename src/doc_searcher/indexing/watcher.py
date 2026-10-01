# Purpose: Keep configured folders searchable while an app/service is running.
# Behavior: Debounce filesystem events, serialize targeted updates, periodically reconcile.
# Usage: Owner must stop(); watchdog is preferred, polling recovers lost/unavailable watches.
import logging
import os
import sys
import threading
import time
from dataclasses import replace
from .service import IndexingService

logger = logging.getLogger(__name__)


class FolderWatcher:
    def __init__(self, db, request_factory, *, debounce=0.35, reconcile_seconds=30):
        self.db, self.request_factory = db, request_factory
        self.debounce, self.reconcile_seconds = debounce, reconcile_seconds
        self.service = IndexingService(db)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._paused = threading.Event()
        self._guard = threading.Lock()
        self._paths = set()
        self._rescan = False
        self._last_event = 0.0
        self._thread = None
        self._observer = None
        self._signature = None
        self._owned_roots = set()
        self.semantic_worker = None
        self._state = dict(state="idle", pending=0, errors=[], generation=0, result={})

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        if os.environ.get("DOC_SEARCHER_EMBEDDING_MODEL") and self.semantic_worker is None:
            from doc_searcher.search.semantic import SemanticMaintainer

            self.semantic_worker = SemanticMaintainer(self.db)
            self.semantic_worker.start()
        self._thread = threading.Thread(target=self._run, name="folder-index-watcher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.service.cancel()
        self._wake.set()
        if self._thread:
            self._thread.join()  # OCR checks cancellation between bounded subprocess polls.
        if self.semantic_worker:
            self.semantic_worker.stop()

    def pause(self):
        self._paused.set()
        self.service.cancel()
        self._enqueue(directory=True)
        with self._guard:
            self._state["state"] = "paused"

    def resume(self):
        self._paused.clear()
        self._wake.set()

    def status(self):
        with self._guard:
            return {
                **self._state,
                "pending": len(self._paths) + int(self._rescan),
                "errors": list(self._state["errors"]),
                "semantic": self.semantic_worker.status()
                if self.semantic_worker
                else {"state": "disabled"},
            }

    def _enqueue(self, paths=(), *, directory=False):
        with self._guard:
            self._paths.update(os.path.abspath(path) for path in paths if path)
            self._rescan |= directory
            self._last_event = time.monotonic()
        self._wake.set()

    def _record_error(self, error):
        logger.warning("Folder watcher: %s", error)
        with self._guard:
            self._state["errors"] = list(dict.fromkeys(self._state["errors"] + [str(error)]))[-20:]

    def _observe(self, request):
        from watchdog.events import FileSystemEventHandler

        if sys.platform == "darwin":
            # FSEvents stop from our owner thread crashes the macOS/Python 3.13 runtime.
            from watchdog.observers.polling import PollingObserver as Observer
        else:
            from watchdog.observers import Observer
        owner = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.event_type not in {"created", "modified", "deleted", "moved", "closed"}:
                    return
                # Directory modification accompanies every file write; rescanning on it
                # would defeat targeted updates. Directory moves/create/delete need scans.
                if event.is_directory and event.event_type == "modified":
                    return
                owner._enqueue(
                    (event.src_path, getattr(event, "dest_path", "")), directory=event.is_directory
                )

        observer = Observer(timeout=0.5)
        available, _ = self.service.scanner.partition_directories(request.roots)
        for root in available:
            observer.schedule(Handler(), root, recursive=request.include_subdirectories)
        observer.start()
        self._observer = observer

    def _reset_observer(self, request):
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        try:
            self._observe(request)
        except ImportError:
            with self._guard:
                self._state["monitor"] = "reconciliation"
        except OSError as exc:
            self._record_error(exc)
        self._enqueue(directory=True)

    def _run(self):
        next_scan = 0.0
        try:
            while not self._stop.is_set():
                if self._paused.is_set():
                    self._stop.wait(0.1)
                    continue
                self.service.indexer.reset_cancellation()
                try:
                    request = self.request_factory()
                except Exception as exc:
                    self._record_error(exc)
                    self._stop.wait(0.5)
                    continue
                self._owned_roots.update(request.roots)
                signature = (
                    tuple(request.roots),
                    request.include_subdirectories,
                    tuple(request.exclude_patterns),
                    tuple(self.service.scanner.partition_directories(request.roots)[0]),
                )
                if signature != self._signature:
                    self._signature = signature
                    self._reset_observer(request)
                now = time.monotonic()
                with self._guard:
                    ready = now - self._last_event >= self.debounce
                    rescan = (ready and self._rescan) or now >= next_scan
                    paths = list(self._paths) if ready else []
                    if ready:
                        self._paths.clear()
                        self._rescan = False
                if not rescan and not paths:
                    self._wake.wait(0.1)
                    self._wake.clear()
                    continue
                self.service.indexer.request_is_current = lambda current=request: (
                    self.request_factory() == current and not self._stop.is_set()
                )
                with self._guard:
                    self._state["state"] = "indexing"
                try:
                    if rescan:
                        # Scoped reconciliation preserves documents owned by other clients.
                        result = self.service.run(
                            replace(
                                request,
                                scope=request.scope
                                if request.scope is not None
                                else list(self._owned_roots),
                            )
                        )
                        next_scan = time.monotonic() + (
                            self.reconcile_seconds if self._observer else 1
                        )
                        if paths:
                            self._enqueue(paths)  # preserve events whose timestamps did not change
                    else:
                        result = self.service.update_paths(request, paths)
                    if result.get("retry_paths"):
                        self._enqueue(result["retry_paths"])
                    if rescan:
                        for path in result.get("scan_error_paths", []):
                            self._record_error("Cannot scan: " + path)
                    for error in result.get("scan_errors", []):
                        self._record_error(error)
                    with self._guard:
                        self._state.update(
                            state="paused" if self._paused.is_set() else "idle", result=result
                        )
                        if result["indexed"] or result["deleted"] or result["failed"]:
                            self._state["generation"] += 1
                except Exception as exc:
                    self._record_error(exc)
                    self._enqueue(paths, directory=True)
                    next_scan = time.monotonic() + 1
                    self._stop.wait(0.5)
        finally:
            if self._observer:
                self._observer.stop()
                self._observer.join()
            self.db.close()
            with self._guard:
                self._state["state"] = "stopped"
