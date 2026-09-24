# Purpose: Cross-platform OS helpers for opening files and revealing in file manager.
# What the code does:
#   - Opens files using default system applications on Windows, macOS, and Linux.
#   - Reveals files in Windows Explorer or macOS Finder.
#   - Formats file sizes and timestamps into human-readable strings.
# Usage notes, dependencies, or assumptions:
#   - Uses the central OS detector so UI styling and native actions agree.
#   - Launch failures (LAUNCH_ERRORS) are logged and return False; other errors propagate.

import logging
import os
import subprocess
from datetime import datetime
from typing import Optional

from .os_detector import CURRENT_OS, OSInfo

logger = logging.getLogger(__name__)

# Launch failures: the program is missing/not permitted (OSError) or exits non-zero.
LAUNCH_ERRORS = (OSError, subprocess.CalledProcessError)


def open_file_with_default_app(file_path: str, os_info: Optional[OSInfo] = None) -> bool:
    """Open a file with the operating system's default viewer/editor."""
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return False

    active_os = os_info or CURRENT_OS
    try:
        if active_os.is_windows:
            os.startfile(abs_path)  # type: ignore[attr-defined,unused-ignore]
        elif active_os.is_macos:
            subprocess.run(["open", abs_path], check=True)
        elif active_os.is_linux:
            subprocess.run(["xdg-open", abs_path], check=True)
        else:
            return False
        return True
    except LAUNCH_ERRORS as e:
        logger.warning("Failed to open file %s: %s", abs_path, e)
        return False


def reveal_in_file_manager(file_path: str, os_info: Optional[OSInfo] = None) -> bool:
    """Reveal a file using the detected platform's native file manager."""
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return False

    active_os = os_info or CURRENT_OS
    try:
        if active_os.is_windows:
            subprocess.run(["explorer", f"/select,{abs_path}"], check=True)
        elif active_os.is_macos:
            subprocess.run(["open", "-R", abs_path], check=True)
        elif active_os.is_linux:
            subprocess.run(["xdg-open", os.path.dirname(abs_path)], check=True)
        else:
            return False
        return True
    except LAUNCH_ERRORS as e:
        logger.warning("Failed to reveal file %s: %s", abs_path, e)
        return False


def format_file_size(size_bytes: int) -> str:
    """Format bytes to human readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_timestamp(ts: float) -> str:
    """Format unix timestamp to readable date/time."""
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M")
