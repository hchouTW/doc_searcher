"""Reproducible benchmark datasets (Task 5.1).

Datasets are generated deterministically (fixed random seed) into pytest temp directories and
never committed:
  - corpus_1k / corpus_10k: 1,000 / 10,000 small .txt/.md/.csv files (~1.5 KB each) of mixed
    Traditional Chinese and English text with dates and numbers, spread over 20 folders.
    corpus_10k only runs with DOC_SEARCHER_BENCH_10K=1 (cold indexing takes minutes).
  - large_files: a 200-page PDF, a ~5 MB text file, and a 20,000-row spreadsheet.
"""

import os
import random
from pathlib import Path

import pytest

from doc_searcher.indexing.service import IndexingService, IndexRequest
from doc_searcher.storage.database import Database

SEED = 20260924
WORDS_EN = (
    "budget report quarterly revenue forecast contract project meeting summary plan risk "
    "invoice analysis policy review schedule vendor audit strategy customer product"
).split()
WORDS_ZH = "預算 報告 季度 營收 預測 合約 專案 會議 摘要 計畫 風險 發票 分析 政策 審查 時程 供應商 稽核 策略 客戶 產品".split()


def _paragraph(rng: random.Random, words: int) -> str:
    tokens = []
    for _ in range(words):
        roll = rng.random()
        if roll < 0.45:
            tokens.append(rng.choice(WORDS_EN))
        elif roll < 0.9:
            tokens.append(rng.choice(WORDS_ZH))
        elif roll < 0.95:
            tokens.append(
                f"{rng.randint(2019, 2026)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            )
        else:
            tokens.append(str(rng.randint(100, 99999)))
    return " ".join(tokens)


def build_corpus(root: Path, count: int) -> Path:
    rng = random.Random(SEED + count)
    for index in range(count):
        folder = root / f"dept_{index % 20:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        suffix = (".txt", ".md", ".csv")[index % 3]
        text = "\n".join(_paragraph(rng, 60) for _ in range(4))
        (folder / f"doc_{index:05d}{suffix}").write_text(text, encoding="utf-8")
    return root


@pytest.fixture(scope="session")
def corpus_1k(tmp_path_factory):
    return build_corpus(tmp_path_factory.mktemp("corpus_1k"), 1_000)


@pytest.fixture(scope="session")
def corpus_10k(tmp_path_factory):
    if os.environ.get("DOC_SEARCHER_BENCH_10K") != "1":
        pytest.skip("set DOC_SEARCHER_BENCH_10K=1 to run the 10,000-file benchmarks")
    return build_corpus(tmp_path_factory.mktemp("corpus_10k"), 10_000)


@pytest.fixture(scope="session")
def indexed_1k(tmp_path_factory, corpus_1k):
    """A database already holding corpus_1k (built once per session)."""
    path = tmp_path_factory.mktemp("indexed_1k") / "index.db"
    db = Database(str(path))
    IndexingService(db).run(IndexRequest(roots=[str(corpus_1k)]))
    db.close()
    return path


@pytest.fixture(scope="session")
def large_files(tmp_path_factory):
    import openpyxl
    import pymupdf

    root = tmp_path_factory.mktemp("large")
    rng = random.Random(SEED)

    pdf = pymupdf.open()
    for _ in range(200):
        page = pdf.new_page()
        page.insert_text((50, 60), _paragraph(rng, 120)[:3000], fontsize=8)
    pdf.save(str(root / "large.pdf"))
    pdf.close()

    with open(root / "large.txt", "w", encoding="utf-8") as handle:
        while handle.tell() < 5 * 1024 * 1024:
            handle.write(_paragraph(rng, 200) + "\n")

    workbook = openpyxl.Workbook(write_only=True)
    sheet = workbook.create_sheet("data")
    for row in range(20_000):
        sheet.append([row, rng.choice(WORDS_EN), rng.choice(WORDS_ZH), rng.randint(1, 10**6)])
    workbook.save(str(root / "large.xlsx"))
    return root
