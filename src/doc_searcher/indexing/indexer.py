# Purpose: Document parsing and full-text indexer coordinator.
# What the code does:
#   - Coordinates parsing, tokenization, and metadata storage including mtime/ctime.
#   - Supports progress reporting plus cooperative pause, resume, and cancellation controls.
#   - Implements incremental updates and deletion handling.
#   - A document that fails to parse is stored with its error and counted as failed; it never
#     stops the batch.
# Usage notes, dependencies, or assumptions:
#   - Integrates parsers, database, and doc_searcher.search.text_helper.

import os
import threading
from typing import Callable, Optional, List, Dict

from doc_searcher.parsers import ParseStatus, parse_file
from doc_searcher.search.text_helper import tokenize_for_fts
from doc_searcher.storage.database import Database


class DocumentIndexer:
    """Manages document text extraction and index building."""

    def __init__(self, db: Database):
        self.db = db
        self._is_cancelled = False
        self._resume_event = threading.Event()
        self._resume_event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._is_cancelled

    @property
    def is_paused(self) -> bool:
        return not self._resume_event.is_set()

    def pause(self):
        """Pause at the next safe checkpoint."""
        self._resume_event.clear()

    def resume(self):
        """Resume a paused indexing process."""
        self._resume_event.set()

    def wait_until_ready(self) -> bool:
        """Block while paused and return whether processing should continue."""
        self._resume_event.wait()
        return not self._is_cancelled

    def cancel(self):
        """Request cancellation of indexing process."""
        self._is_cancelled = True
        self._resume_event.set()

    def reset_cancellation(self):
        self._is_cancelled = False

    def index_single_file(self, file_path: str) -> bool:
        """Parse and index a single document file."""
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            self.db.delete_document(abs_path)
            return False

        try:
            st = os.stat(abs_path)
            file_size = st.st_size
            mtime = st.st_mtime
            ctime = getattr(st, "st_birthtime", st.st_ctime)
            ext = os.path.splitext(abs_path)[1].lower().lstrip(".")
        except Exception as e:
            print(f"[Indexer] Cannot stat {abs_path}: {e}")
            return False

        # parse_file never raises for document problems; a bad file becomes an error record.
        extracted = parse_file(abs_path)
        if extracted.status is ParseStatus.UNSUPPORTED:
            return False

        if extracted.error:
            self.db.save_document_index(
                file_path=abs_path,
                file_type=ext,
                file_size=file_size,
                mtime=mtime,
                ctime=ctime,
                segments=[],
                error=extracted.error,
            )
            return False

        # Process and tokenize segments
        prepared_segments: List[Dict[str, str]] = []
        for seg in extracted.segments:
            text = seg.text.strip()
            if not text:
                continue
            tokenized = tokenize_for_fts(text)
            prepared_segments.append(
                {
                    "segment_id": seg.segment_id,
                    "segment_type": seg.segment_type,
                    "content": text,
                    "tokenized_content": tokenized,
                }
            )

        self.db.save_document_index(
            file_path=abs_path,
            file_type=ext,
            file_size=file_size,
            mtime=mtime,
            ctime=ctime,
            segments=prepared_segments,
            error=None,
        )
        return True

    def run_batch_indexing(
        self,
        to_index_files: List[str],
        to_delete_files: List[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        reset_cancellation: bool = True,
    ) -> Dict[str, int]:
        """Execute indexing on list of files with progress notifications.

        Returns:
            Counts for indexed, deleted, and failed files, plus cancellation state.
        """
        if reset_cancellation:
            self.reset_cancellation()
        stats = {"indexed": 0, "deleted": 0, "failed": 0}
        total = len(to_delete_files) + len(to_index_files)
        completed = 0

        # 1. Process deletions
        for del_path in to_delete_files:
            if not self.wait_until_ready():
                break
            completed += 1
            if progress_callback:
                progress_callback(completed, total, f"移除索引：{os.path.basename(del_path)}")
            self.db.delete_document(del_path)
            stats["deleted"] += 1

        # 2. Process additions/updates
        for file_path in to_index_files:
            if not self.wait_until_ready():
                break

            completed += 1
            if progress_callback:
                progress_callback(completed, total, os.path.basename(file_path))

            success = self.index_single_file(file_path)
            if success:
                stats["indexed"] += 1
            else:
                stats["failed"] += 1

        stats["cancelled"] = self._is_cancelled
        return stats
