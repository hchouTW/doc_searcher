"""Regression tests for the headless CLI (doc-searcher --dir/--search)."""

import pytest

from doc_searcher import cli as main
from doc_searcher.config import AppConfig
from doc_searcher.storage.database import Database


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    monkeypatch.setenv("DOC_SEARCHER_DATA_DIR", str(path))
    return path


@pytest.fixture
def roots(tmp_path):
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    for root, word in ((root_a, "alpha"), (root_b, "beta")):
        root.mkdir()
        (root / f"{word}.txt").write_text(f"{word} keyword text", encoding="utf-8")
        (root / f"{word}_2.txt").write_text(f"{word} second file", encoding="utf-8")
    return root_a, root_b


def indexed_paths():
    db = Database(AppConfig().db_path)
    try:
        return set(db.get_all_indexed_paths())
    finally:
        db.close()


def run(*argv):
    return main.main(list(argv))


def test_rescanning_one_root_preserves_other_root(data_dir, roots):
    root_a, root_b = roots
    assert run("--dir", str(root_a), "--search", "keyword") == 0
    assert run("--dir", str(root_b), "--search", "keyword") == 0
    b_paths = {str(p) for p in root_b.iterdir()}
    assert b_paths <= indexed_paths()

    (root_a / "alpha_2.txt").unlink()
    assert run("--dir", str(root_a), "--search", "keyword") == 0

    paths = indexed_paths()
    assert b_paths <= paths
    assert str(root_a / "alpha_2.txt") not in paths
    assert str(root_a / "alpha.txt") in paths


def test_unavailable_root_preserves_index_and_fails(data_dir, roots, capsys):
    root_a, root_b = roots
    assert run("--dir", str(root_a), "--search", "keyword") == 0
    before = indexed_paths()

    moved = root_a.with_name("root_a_offline")
    root_a.rename(moved)
    assert run("--dir", str(root_a), "--search", "keyword") != 0
    assert indexed_paths() == before
    assert "無法存取" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["--dir", "x"], ["--search", "x"]])
def test_partial_cli_arguments_are_usage_errors(argv, data_dir, capsys):
    with pytest.raises(SystemExit) as exc:
        run(*argv)
    assert exc.value.code == 2
    assert "--dir" in capsys.readouterr().err


def test_invalid_type_filter_is_usage_error(data_dir, roots):
    with pytest.raises(SystemExit) as exc:
        run("--dir", str(roots[0]), "--search", "x", "--type", "bogus")
    assert exc.value.code == 2
