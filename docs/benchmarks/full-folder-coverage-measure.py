# Purpose: Measure real watcher latency and local OCR/dense retrieval in an isolated corpus.
# Behavior: Record create/update timing plus Chinese printed-scan and bilingual query recall.
# Usage: PYTHONPATH=src DOC_SEARCHER_EMBEDDING_MODEL=local-E5-path python this-file output.json
import json
import os
import platform
import tempfile
import time
from io import BytesIO
from pathlib import Path
from doc_searcher.indexing.service import IndexRequest, IndexingService
from doc_searcher.indexing.watcher import FolderWatcher
from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.storage.database import Database


def main(output):
    result = dict(platform=platform.platform(), python=platform.python_version())
    with tempfile.TemporaryDirectory(prefix="folder-coverage-") as temporary:
        base = Path(temporary)
        root = base / "docs"
        root.mkdir()
        db = Database(str(base / "index.db"))
        watcher = FolderWatcher(db, lambda: IndexRequest([str(root)]))
        watcher.start()
        latencies = []
        try:
            for number in range(6):
                path = root / "live.txt"
                term = f"LATENCYMARKER{number}"
                started = time.monotonic()
                path.write_text("會議 " + term)
                deadline = started + 5
                while time.monotonic() < deadline and not DocumentSearcher(db).search(term):
                    time.sleep(0.02)
                elapsed = time.monotonic() - started
                assert DocumentSearcher(db).search(term) and elapsed < 5
                latencies.append(round(elapsed, 3))
            result["watcher_seconds"] = latencies
            result["watcher_max_seconds"] = max(latencies)
        finally:
            watcher.stop()
        # Printed Chinese image-only PDF: actual pixels, not a hidden text layer.
        from PIL import Image, ImageDraw, ImageFont
        import pymupdf

        fonts = (
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
        )
        font_path = next((p for p in fonts if os.path.isfile(p)), None)
        if font_path:
            image = Image.new("RGB", (1800, 400), "white")
            ImageDraw.Draw(image).text(
                (50, 90),
                "會議記錄 文件檢索 7429",
                font=ImageFont.truetype(font_path, 100),
                fill="black",
            )
            data = BytesIO()
            image.save(data, format="PNG")
            with pymupdf.open() as document:
                page = document.new_page(width=900, height=200)
                page.insert_image(page.rect, stream=data.getvalue())
                document.save(root / "scanned.pdf")
            started = time.monotonic()
            IndexingService(db).run(IndexRequest([str(root)]))
            matches = DocumentSearcher(db).search("會議")
            result["chinese_ocr_seconds"] = round(time.monotonic() - started, 3)
            result["chinese_ocr_found"] = any(item.filename == "scanned.pdf" for item in matches)
            assert result["chinese_ocr_found"]
        (root / "hr.txt").write_text("員工休假規定：每位同仁每年可申請十五天帶薪假期。")
        (root / "finance.txt").write_text("年度財務報表記錄營業收入、支出及資產負債。")
        IndexingService(db).run(IndexRequest([str(root)]))
        if os.environ.get("DOC_SEARCHER_EMBEDDING_MODEL"):
            started = time.monotonic()
            page = DocumentSearcher(db).search_page(
                "annual paid leave policy", search_mode="hybrid"
            )
            result["semantic_warm_build_query_seconds"] = round(time.monotonic() - started, 3)
            result["semantic_english_top"] = page.items[0].filename
            page = DocumentSearcher(db).search_page("放假辦法", search_mode="hybrid")
            result["semantic_chinese_top"] = page.items[0].filename
            assert result["semantic_english_top"] == result["semantic_chinese_top"] == "hr.txt"
        db.close()
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys

    main(sys.argv[1])
