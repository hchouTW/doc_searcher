"""Keep the compatibility requirement files in step with pyproject.toml."""

from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")  # Python 3.11+

ROOT = Path(__file__).parent.parent


def requirement_lines(name):
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith(("#", "-r"))}


def test_requirement_files_match_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert requirement_lines("requirements.txt") == set(project["dependencies"])
    assert requirement_lines("requirements-mcp.txt") == set(project["optional-dependencies"]["mcp"])
    assert requirement_lines("requirements-dev.txt") <= set(project["optional-dependencies"]["dev"])
