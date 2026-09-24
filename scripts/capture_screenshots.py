# Purpose: Reproduce README screenshots using synthetic documents and isolated settings.
# Behavior: Renders identical search results in both themes and the advanced filter panel.
# Usage: QT_QPA_PLATFORM=offscreen python scripts/capture_screenshots.py
# Requires the installed project/PySide6; never opens or modifies the user's index.

import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from doc_searcher.config import AppConfig
from doc_searcher.desktop.main_window import MainWindow
from doc_searcher.desktop.theme import get_active_theme
from doc_searcher.search.searcher import SearchResultItem, SegmentMatch


def main():
    output = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    font = app.font()
    font.setPointSize(10)
    app.setFont(font)
    with tempfile.TemporaryDirectory(prefix="doc-searcher-screenshots-") as directory:
        os.environ["DOC_SEARCHER_DATA_DIR"] = directory
        config = AppConfig(Path(directory) / "config.json")
        config.db_path = str(Path(directory) / "index.db")
        window = MainWindow(config)
        window.resize(1680, 900)
        window.show()
        window.splitter.setSizes([300, 840, 540])
        window.search_input.setText("專案 AND 預算")
        window.search_timer.stop()
        results = []
        for index, (name, kind, excerpt) in enumerate(
            [
                ("年度專案預算.pdf", "pdf", "年度專案的預算分配包含研發、設備與教育訓練。"),
                ("專案執行計畫.docx", "docx", "本專案依季度檢視預算與執行進度。"),
                ("部門預算彙整.xlsx", "xlsx", "各部門專案預算將於月底完成審核。"),
                ("季度工作報告.pptx", "pptx", "本季專案成果與下季預算需求。"),
            ]
        ):
            snippet = excerpt.replace("專案", "<mark>專案</mark>").replace(
                "預算", "<mark>預算</mark>"
            )
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
                    segments=[
                        SegmentMatch(segment_id="1", segment_type="page", snippets=[snippet])
                    ],
                )
            )
        window.table.horizontalHeader().setSortIndicator(1, Qt.AscendingOrder)
        window.table.set_results(results)
        window.index_summary_label.setText("已建立 4 份文件索引")
        window.results_count_label.setText("找到 4 份文件")
        window.status_label.setText("搜尋完成 · 4 份文件")
        for mode in ("light", "dark"):
            window.theme_mode = mode
            window.apply_theme(get_active_theme(mode))
            app.processEvents()
            window.grab().save(str(output / f"doc_searcher_{mode}.png"))
            window.btn_advanced_filters.setChecked(True)
            app.processEvents()
            window.advanced_dialog.grab().save(str(output / f"doc_searcher_advanced_{mode}.png"))
            window.btn_advanced_filters.setChecked(False)
        # Preserve the original README image path for external links.
        shutil.copyfile(
            output / "doc_searcher_advanced_light.png", output / "doc_searcher_advanced.png"
        )
        window.close()


if __name__ == "__main__":
    main()
