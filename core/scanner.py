# Purpose: Directory scanner and file filtering engine.
# What the code does:
#   - Traverses target folders, optionally including all subdirectories, to discover documents.
#   - Supports cooperative checkpoints so background scans can pause or cancel safely.
#   - Deduplicates overlapping roots and preserves indexes for temporarily unavailable roots.
#   - Prunes hidden/system folders and user-configurable glob/name exclusions. Relative patterns
#     only apply below each scanned root; absolute patterns (/... or C:/...) match the full path.
#   - Compares with indexed metadata to identify new, modified, and deleted files.
# Usage notes, dependencies, or assumptions:
#   - Uses standard library os and pathlib.

import os
import fnmatch
import re
from typing import Callable, List, Dict, Optional, Set, Tuple

from parsers import SUPPORTED_EXTENSIONS


_ABSOLUTE_PATTERN_RE = re.compile(r"^(/|[A-Za-z]:/)")


def is_absolute_pattern(pattern: str) -> bool:
    """Return whether a normalized ("/"-separated) exclusion pattern is an absolute path."""
    return bool(_ABSOLUTE_PATTERN_RE.match(pattern))


class FileScanner:
    """Scans and filters supported document files across directories."""

    def __init__(self, supported_extensions: Set[str] = SUPPORTED_EXTENSIONS):
        self.supported_extensions = supported_extensions

    def is_valid_document_file(self, file_name: str) -> bool:
        """Check if filename meets indexing criteria."""
        # Exclude temporary MS Office lock files like ~$document.docx
        if file_name.startswith("~$"):
            return False
        # Exclude hidden files / system metadata
        if file_name.startswith("."):
            return False
        if file_name.lower() in {"thumbs.db", "desktop.ini"}:
            return False
        
        ext = os.path.splitext(file_name)[1].lower()
        return ext in self.supported_extensions

    @classmethod
    def matches_exclusion(cls, path: str, patterns: List[str], root: Optional[str] = None) -> bool:
        """Match a path against folder names, file names, or glob patterns.

        With root, relative patterns only see the part of path below root, so a folder above
        the search folder (e.g. pattern "temp" and root ~/temp/docs) never excludes it.
        """
        normalized = path.replace("\\", "/")
        relative = normalized
        if root is not None and cls.is_path_within_directory(path, root):
            relative = "/" + os.path.relpath(path, root).replace("\\", "/")
        name = os.path.basename(normalized)
        parts = relative.split("/")
        for raw_pattern in patterns:
            pattern = raw_pattern.strip().replace("\\", "/")
            if not pattern:
                continue
            if is_absolute_pattern(pattern):
                if fnmatch.fnmatch(normalized, pattern):
                    return True
            elif pattern in parts or fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative, pattern):
                return True
        return False

    @staticmethod
    def is_path_within_directory(path: str, directory: str) -> bool:
        """Return whether path is inside directory, including cross-drive safety."""
        abs_path = os.path.normcase(os.path.abspath(path))
        abs_directory = os.path.normcase(os.path.abspath(directory))
        try:
            return os.path.commonpath([abs_path, abs_directory]) == abs_directory
        except ValueError:
            return False

    def normalize_directories(self, directories: List[str]) -> List[str]:
        """Remove duplicate and nested roots that would scan the same files twice."""
        normalized: List[str] = []
        for directory in directories:
            candidate = os.path.abspath(directory)
            if any(
                self.is_path_within_directory(candidate, existing)
                for existing in normalized
            ):
                continue

            normalized = [
                existing
                for existing in normalized
                if not self.is_path_within_directory(existing, candidate)
            ]
            normalized.append(candidate)
        return normalized

    def partition_directories(
        self, directories: List[str]
    ) -> Tuple[List[str], List[str]]:
        """Split configured roots into accessible and temporarily unavailable lists."""
        available = []
        unavailable = []
        for directory in self.normalize_directories(directories):
            try:
                with os.scandir(directory):
                    pass
                available.append(directory)
            except (OSError, PermissionError):
                unavailable.append(directory)
        return available, unavailable

    def scan_directories(
        self,
        directories: List[str],
        include_subdirectories: bool = True,
        checkpoint: Optional[Callable[[], bool]] = None,
        error_callback: Optional[Callable[[str], None]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> List[Tuple[str, float, int]]:
        """Scan specified directories for valid files.

        Subdirectories are included by default. Set include_subdirectories to
        False to scan only files directly inside each selected directory.
        
        Returns:
            List of (abs_path, mtime, file_size) tuples.
        """
        found_files: List[Tuple[str, float, int]] = []
        seen_paths: Set[str] = set()
        visited_directories: Set[Tuple[int, int]] = set()
        exclude_patterns = exclude_patterns or []

        for dir_path in self.normalize_directories(directories):
            if checkpoint and not checkpoint():
                return found_files

            abs_dir = os.path.abspath(dir_path)
            if not os.path.isdir(abs_dir):
                continue

            def _on_walk_error(error: OSError):
                if error_callback:
                    error_callback(error.filename or abs_dir)

            for root, child_directories, files in os.walk(
                abs_dir,
                followlinks=True,
                onerror=_on_walk_error,
            ):
                if checkpoint and not checkpoint():
                    return found_files

                try:
                    root_stat = os.stat(root)
                    directory_id = (root_stat.st_dev, root_stat.st_ino)
                except (OSError, PermissionError):
                    child_directories.clear()
                    if error_callback:
                        error_callback(root)
                    continue

                if directory_id in visited_directories:
                    child_directories.clear()
                    continue
                visited_directories.add(directory_id)

                child_directories[:] = [
                    name for name in child_directories
                    if not name.startswith(".")
                    and not self.matches_exclusion(
                        os.path.join(root, name), exclude_patterns, abs_dir
                    )
                ]

                for f in files:
                    if checkpoint and not checkpoint():
                        return found_files

                    if self.is_valid_document_file(f):
                        full_path = os.path.abspath(os.path.join(root, f))
                        if self.matches_exclusion(full_path, exclude_patterns, abs_dir):
                            continue
                        if full_path in seen_paths:
                            continue
                        seen_paths.add(full_path)

                        try:
                            st = os.stat(full_path)
                            found_files.append((full_path, st.st_mtime, st.st_size))
                        except (OSError, PermissionError):
                            # Skip unreadable files
                            if error_callback:
                                error_callback(full_path)
                            continue

                if not include_subdirectories:
                    break

        return found_files

    def calculate_changes(
        self,
        current_files: List[Tuple[str, float, int]],
        indexed_files: Dict[str, Tuple[float, int]],
        preserved_directories: Optional[List[str]] = None,
    ) -> Tuple[List[str], List[str]]:
        """Compare current scanned files against indexed files.
        
        Returns:
            (to_index_paths, to_delete_paths)
        """
        to_index = []
        current_paths = set()

        for path, mtime, size in current_files:
            current_paths.add(path)
            if path not in indexed_files:
                # Brand new file
                to_index.append(path)
            else:
                idx_mtime, idx_size = indexed_files[path]
                # If modification time or size changed, re-index
                if abs(mtime - idx_mtime) > 0.001 or size != idx_size:
                    to_index.append(path)

        # Detect files that have been deleted on disk
        preserved_directories = preserved_directories or []
        to_delete = [
            path
            for path in indexed_files
            if path not in current_paths
            and not any(
                self.is_path_within_directory(path, directory)
                for directory in preserved_directories
            )
        ]

        return to_index, to_delete
