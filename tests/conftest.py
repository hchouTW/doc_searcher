"""Shared pytest setup.

Its presence puts tests/ on sys.path, so `fixtures` is importable. It also applies the platform
markers declared in pyproject.toml: a test marked posix/windows/macos is skipped, with a reason,
when not running on that platform, so native-only checks run only on their own CI runners.
"""

import sys

import pytest

_PLATFORMS = {
    "posix": (sys.platform != "win32", "needs POSIX semantics"),
    "windows": (sys.platform == "win32", "needs native Windows"),
    "macos": (sys.platform == "darwin", "needs native macOS"),
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        for marker, (supported, reason) in _PLATFORMS.items():
            if item.get_closest_marker(marker) and not supported:
                item.add_marker(pytest.mark.skip(reason=f"{reason} (running on {sys.platform})"))
