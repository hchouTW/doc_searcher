# Purpose: Reproduce README screenshots using synthetic documents and isolated settings.
# Behavior: Renders identical search results in both themes, the advanced filter panel, and the
#   search mode (Literal/Expanded/Hybrid) dropdown opened over the results controls, once per UI
#   language. Traditional Chinese keeps the original file names; English adds an `_en` suffix.
# Usage: QT_QPA_PLATFORM=offscreen python scripts/capture_screenshots.py
# Requires the installed project/PySide6; never opens or modifies the user's index.

import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication

from doc_searcher.config import AppConfig
from doc_searcher.desktop.main_window import MainWindow
from doc_searcher.desktop.theme import get_active_theme
from doc_searcher.search.searcher import SearchResultItem, SegmentMatch


def grab_mode_dropdown(app, window, target):
    """Save the search-mode combo with its opened list, cropped to the left control column."""
    combo = window.retrieval_mode
    panel = combo.window()
    combo.showPopup()
    app.processEvents()
    popup = combo.view().window()
    top_left = combo.mapTo(panel, QPoint(0, 0))
    base = panel.grab()
    painter = QPainter(base)
    painter.drawPixmap(combo.mapTo(panel, QPoint(0, combo.height())), popup.grab())
    painter.end()
    combo.hidePopup()
    app.processEvents()
    margin = 16
    crop = base.copy(
        max(top_left.x() - margin, 0),
        max(top_left.y() - 200, 0),
        combo.width() + 2 * margin,
        combo.height() + 200 + 120,
    )
    crop.save(str(target))


# Per-language demo content: UI language code, file-name suffix, query, status texts, documents
# (name, type, excerpt, highlighted terms).
DEMOS = (
    {
        "language": "zh-TW",
        "suffix": "",
        "query": "專案 AND 預算",
        "terms": ("專案", "預算"),
        "indexed": "已建立 4 份文件索引",
        "found": "找到 4 份文件",
        "status": "搜尋完成 · 4 份文件",
        "documents": (
            ("年度專案預算.pdf", "pdf", "年度專案的預算分配包含研發、設備與教育訓練。"),
            ("專案執行計畫.docx", "docx", "本專案依季度檢視預算與執行進度。"),
            ("部門預算彙整.xlsx", "xlsx", "各部門專案預算將於月底完成審核。"),
            ("季度工作報告.pptx", "pptx", "本季專案成果與下季預算需求。"),
        ),
    },
    {
        "language": "en-US",
        "suffix": "_en",
        "query": "project AND budget",
        "terms": ("project", "budget"),
        "indexed": "4 documents indexed",
        "found": "4 documents found",
        "status": "Search complete · 4 documents",
        "documents": (
            (
                "Annual Project Budget.pdf",
                "pdf",
                "The annual project budget covers research, equipment and training.",
            ),
            (
                "Project Execution Plan.docx",
                "docx",
                "Each quarter the project reviews its budget and progress.",
            ),
            (
                "Department Budget Summary.xlsx",
                "xlsx",
                "Every department's project budget is reviewed at month end.",
            ),
            (
                "Quarterly Report.pptx",
                "pptx",
                "This quarter's project results and next quarter's budget needs.",
            ),
        ),
    },
)


def demo_results(demo):
    results = []
    for index, (name, kind, excerpt) in enumerate(demo["documents"]):
        snippet = excerpt
        for term in demo["terms"]:
            snippet = snippet.replace(term, f"<mark>{term}</mark>")
        results.append(
            SearchResultItem(
                doc_id=index + 1,
                path=f"/Demo/Documents/{name}",
                filename=name,
                file_type=kind,
                file_size=(index + 1) * 24576,
                mtime=1788220800,
                rank_score=1.0,
                total_matches=2,
                segments=[SegmentMatch(segment_id="1", segment_type="page", snippets=[snippet])],
            )
        )
    return results


def capture(app, output, directory, demo):
    suffix = demo["suffix"]
    config = AppConfig(Path(directory) / f"config{suffix}.json")
    config.db_path = str(Path(directory) / f"index{suffix}.db")
    config.language = demo["language"]
    window = MainWindow(config)
    window.resize(1680, 900)
    window.show()
    window.splitter.setSizes([300, 840, 540])
    window.search_input.setText(demo["query"])
    window.search_timer.stop()
    window.table.horizontalHeader().setSortIndicator(1, Qt.AscendingOrder)
    window.table.set_results(demo_results(demo))
    window.index_summary_label.setText(demo["indexed"])
    window.results_count_label.setText(demo["found"])
    window.status_label.setText(demo["status"])
    for mode in ("light", "dark"):
        window.theme_mode = mode
        window.apply_theme(get_active_theme(mode))
        app.processEvents()
        window.grab().save(str(output / f"doc_searcher{suffix}_{mode}.png"))
        window.btn_advanced_filters.setChecked(True)
        app.processEvents()
        window.advanced_dialog.grab().save(
            str(output / f"doc_searcher_advanced{suffix}_{mode}.png")
        )
        window.btn_advanced_filters.setChecked(False)
        grab_mode_dropdown(app, window, output / f"doc_searcher_modes{suffix}_{mode}.png")
    window.close()


def main():
    output = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    font = app.font()
    font.setPointSize(10)
    app.setFont(font)
    with tempfile.TemporaryDirectory(prefix="doc-searcher-screenshots-") as directory:
        os.environ["DOC_SEARCHER_DATA_DIR"] = directory
        for demo in DEMOS:
            capture(app, output, directory, demo)
        # Preserve the original README image path for external links.
        shutil.copyfile(
            output / "doc_searcher_advanced_light.png", output / "doc_searcher_advanced.png"
        )


if __name__ == "__main__":
    main()
