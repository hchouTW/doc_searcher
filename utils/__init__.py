# Purpose: Utils package initialization.
# What the code does:
#   - Exposes platform, text helpers, and OS detector.
# Usage notes, dependencies, or assumptions:
#   - None.

from .platform_helper import open_file_with_default_app, reveal_in_file_manager, format_file_size, format_timestamp
from .text_helper import tokenize_for_fts, extract_keywords_from_query, generate_highlighted_snippets
from .os_detector import detect_os, CURRENT_OS, OSInfo
