# Purpose: Operating system detection and self-adaptive environment capabilities.
# What the code does:
#   - Detects OS family (Windows, macOS, Linux) and relevant version details.
#   - Exposes normalized architecture and visual defaults for native adaptation.
#   - Exposes OSInfo dataclass with all adaptive parameters.
# Usage notes, dependencies, or assumptions:
#   - Uses standard library platform and sys.

import platform
import re
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class OSInfo:
    """Encapsulates detected operating system attributes and visual adaptation parameters."""

    os_family: str  # 'windows', 'macos', 'linux', or 'other'
    os_name: str  # 'Windows 11', 'Windows 10', 'macOS 15.4', etc.
    build_number: int  # e.g. 22631 on Win 11, 19045 on Win 10, 0 on others
    arch: str  # 'x86_64', 'arm64'
    is_windows: bool
    is_win11: bool
    is_win10: bool
    is_macos: bool
    is_linux: bool
    # Adaptive visual style properties
    font_family: str
    border_radius: int  # 8px for modern Win11 & macOS, 4px for classic Win10
    display_badge: str  # Compact badge for status bar display


def _normalize_arch(machine: str) -> str:
    """Return a stable architecture label across operating systems."""
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
    }
    normalized = machine.strip().lower()
    return aliases.get(normalized, normalized or "unknown")


def _windows_build(version_text: str) -> int:
    """Extract a Windows build number when the native API is unavailable."""
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined,unused-ignore]
    except (AttributeError, OSError):
        numbers = [int(value) for value in re.findall(r"\d+", version_text)]
        return max(numbers, default=0)


def detect_os(
    system_name: Optional[str] = None,
    release: Optional[str] = None,
    machine: Optional[str] = None,
    mac_version: Optional[str] = None,
    windows_build: Optional[int] = None,
) -> OSInfo:
    """Analyze the runtime and return normalized, testable OS capabilities.

    Optional values are primarily useful to callers that need to inspect a target
    environment or test each supported platform without running on that platform.
    """
    detected_system = (system_name or platform.system()).strip()
    detected_release = release if release is not None else platform.release()
    arch = _normalize_arch(machine if machine is not None else platform.machine())

    is_windows = detected_system.casefold() == "windows"
    is_macos = detected_system.casefold() in {"darwin", "macos"}
    is_linux = detected_system.casefold() == "linux"

    is_win11 = False
    is_win10 = False
    build_num = 0
    os_family = "other"
    os_name = detected_system or "Unknown OS"

    if is_windows:
        os_family = "windows"
        build_num = (
            windows_build if windows_build is not None else _windows_build(platform.version())
        )
        is_win11 = build_num >= 22000 or (build_num == 0 and detected_release == "11")
        is_win10 = 10240 <= build_num < 22000 or (build_num == 0 and detected_release == "10")
        if is_win11:
            version_label = "Windows 11"
        elif is_win10:
            version_label = "Windows 10"
        else:
            version_label = f"Windows {detected_release}" if detected_release else "Windows"
        os_name = f"{version_label} (Build {build_num})" if build_num else version_label

        # Adaptive typography for Windows
        if is_win11:
            font_family = '"Segoe UI Variable Text", "Segoe UI", "Microsoft JhengHei UI", "Microsoft JhengHei", sans-serif'
            border_radius = 8
        else:
            font_family = (
                '"Segoe UI", "Microsoft JhengHei UI", "Microsoft JhengHei", Arial, sans-serif'
            )
            border_radius = 4

        display_badge = "🪟 " + (
            "Windows 11" if is_win11 else "Windows 10" if is_win10 else "Windows"
        )

    elif is_macos:
        os_family = "macos"
        mac_ver = mac_version if mac_version is not None else platform.mac_ver()[0]
        mac_ver = mac_ver or detected_release or "unknown"
        os_name = f"macOS {mac_ver} ({arch})"
        font_family = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", "PingFang TC", "Microsoft JhengHei", sans-serif'
        border_radius = 8
        display_badge = f"🍎 macOS ({arch})"

    elif is_linux:
        os_family = "linux"
        os_name = f"Linux ({detected_release})" if detected_release else "Linux"
        font_family = 'Ubuntu, Cantarell, "DejaVu Sans", "Noto Sans CJK TC", sans-serif'
        border_radius = 6
        display_badge = "🐧 Linux"

    else:
        font_family = 'Arial, "Noto Sans CJK TC", sans-serif'
        border_radius = 4
        display_badge = f"💻 {os_name}"

    return OSInfo(
        os_family=os_family,
        os_name=os_name,
        build_number=build_num,
        arch=arch,
        is_windows=is_windows,
        is_win11=is_win11,
        is_win10=is_win10,
        is_macos=is_macos,
        is_linux=is_linux,
        font_family=font_family,
        border_radius=border_radius,
        display_badge=display_badge,
    )


# Singleton instance for quick access
CURRENT_OS = detect_os()
