"""Tests for operating-system detection and native action adaptation."""

import os
from pathlib import Path
from unittest.mock import patch

from doc_searcher.platform.os_detector import detect_os
from doc_searcher.platform.platform_helper import open_file_with_default_app, reveal_in_file_manager


def test_detects_and_normalizes_supported_operating_systems():
    windows = detect_os("Windows", release="11", machine="AMD64", windows_build=22631)
    macos = detect_os("Darwin", release="24.0", machine="arm64", mac_version="15.0")
    linux = detect_os("Linux", release="6.8.0", machine="aarch64")

    assert windows.os_family == "windows"
    assert windows.is_windows and windows.is_win11 and windows.arch == "x86_64"
    assert macos.os_family == "macos"
    assert macos.is_macos and macos.arch == "arm64"
    assert linux.os_family == "linux"
    assert linux.is_linux and linux.arch == "arm64"


def test_windows_build_selects_compatible_visual_style():
    windows_10 = detect_os("Windows", machine="AMD64", windows_build=19045)
    windows_11 = detect_os("Windows", machine="AMD64", windows_build=22631)

    assert windows_10.is_win10 and windows_10.border_radius == 4
    assert windows_11.is_win11 and windows_11.border_radius == 8


def test_unknown_system_is_not_misidentified_as_linux():
    detected = detect_os("FreeBSD", release="14.1", machine="amd64")

    assert detected.os_family == "other"
    assert not detected.is_windows
    assert not detected.is_macos
    assert not detected.is_linux


def test_native_open_command_adapts_to_macos(tmp_path: Path):
    document = tmp_path / "report.pdf"
    document.touch()
    macos = detect_os("Darwin", machine="arm64", mac_version="15.0")

    with patch("doc_searcher.platform.platform_helper.subprocess.run") as run:
        assert open_file_with_default_app(str(document), macos)

    run.assert_called_once_with(["open", os.path.abspath(document)], check=True)


def test_native_open_uses_windows_shell(tmp_path: Path):
    document = tmp_path / "report.pdf"
    document.touch()
    windows = detect_os("Windows", release="11", machine="AMD64", windows_build=22631)

    with patch("doc_searcher.platform.platform_helper.os.startfile", create=True) as startfile:
        assert open_file_with_default_app(str(document), windows)

    startfile.assert_called_once_with(os.path.abspath(document))


def test_native_reveal_command_adapts_to_linux(tmp_path: Path):
    document = tmp_path / "report.pdf"
    document.touch()
    linux = detect_os("Linux", release="6.8.0", machine="x86_64")

    with patch("doc_searcher.platform.platform_helper.subprocess.run") as run:
        assert reveal_in_file_manager(str(document), linux)

    run.assert_called_once_with(["xdg-open", os.path.dirname(os.path.abspath(document))], check=True)


def test_unknown_system_fails_safely_without_launching(tmp_path: Path):
    document = tmp_path / "report.pdf"
    document.touch()
    unknown = detect_os("FreeBSD", release="14.1", machine="amd64")

    with patch("doc_searcher.platform.platform_helper.subprocess.run") as run:
        assert not open_file_with_default_app(str(document), unknown)
        assert not reveal_in_file_manager(str(document), unknown)

    run.assert_not_called()
