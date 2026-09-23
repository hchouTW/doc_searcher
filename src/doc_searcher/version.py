# Purpose: Single source of truth for the application version and its release history.
# What the code does:
#   - Declares APP_VERSION, shown in the status bar and the About/Version Info dialog.
#   - Lists CHANGELOG entries (version, date, highlights) rendered in that dialog.
# Usage notes, dependencies, or assumptions:
#   - Read by packaging/doc_searcher_mac.spec (bundle version) and packaging/doc_searcher_win.spec
#     (exe version resource, which packaging/installer_inno.iss reads back); edit only here.

from typing import List, Tuple

APP_VERSION = "1.2.0"

# Newest first: (version, release_date, [highlight, ...])
CHANGELOG: List[Tuple[str, str, List[str]]] = [
    (
        "1.2.0",
        "2026-09-24",
        [
            "directory_management",
            "responsive_search_help",
            "persistent_last_updated",
            "remove_os_badge",
        ],
    ),
    (
        "1.1.0",
        "2026-09-23",
        [
            "status_bar_redesign",
            "os_badge_theme_fix",
            "filters_merged_into_advanced_panel",
            "language_segmented_control",
            "wcag_aa_contrast_pass",
        ],
    ),
    (
        "1.0.0",
        "2026-01-01",
        [
            "initial_release",
        ],
    ),
]
