# Purpose: Main entry point for Document Searcher application.
# What the code does:
#   - Supports both GUI mode (default) and CLI mode for automation/testing.
#   - In CLI mode: scans directory, indexes files, runs search, and prints highlighted results.
#   - In GUI mode: launches PySide6 desktop interface.
# Usage notes, dependencies, or assumptions:
#   - GUI: python main.py
#   - CLI: python main.py --dir /path/to/folder --search "關鍵字"

import sys
import os
import argparse
from pathlib import Path

# Ensure package modules can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_cli_mode(folder: str, query: str, type_filter: str = "all"):
    """Run headless search via terminal for quick testing or scripts."""
    from core.config import AppConfig
    from core.database import Database
    from core.scanner import FileScanner
    from core.indexer import DocumentIndexer
    from core.searcher import DocumentSearcher

    abs_dir = os.path.abspath(folder)
    print(f"[*] 掃描目錄：{abs_dir}")
    
    config = AppConfig()
    db = Database(config.db_path)
    scanner = FileScanner()
    indexer = DocumentIndexer(db)

    available_directories, unavailable_directories = scanner.partition_directories(
        [abs_dir]
    )
    if not available_directories:
        print("[!] 檢索目錄目前無法存取；既有索引已保留，未進行變更。")
        db.close()
        return

    files = scanner.scan_directories(available_directories)
    print(f"[*] 找到 {len(files)} 個支援的文件檔案，開始建立/更新索引...")

    current_indexed = db.get_all_indexed_paths()
    to_index, to_delete = scanner.calculate_changes(
        files,
        current_indexed,
        preserved_directories=unavailable_directories,
    )

    if to_index or to_delete:
        print(f"[*] 增量更新中 (新增/變更: {len(to_index)} 個，刪除: {len(to_delete)} 個)...")
        stats = indexer.run_batch_indexing(to_index, to_delete)
        print(f"[✓] 索引更新完成：{stats}")
    else:
        print("[✓] 索引已是最新狀態。")

    print(f"[*] 執行檢索關鍵字：'{query}' (篩選: {type_filter})")
    searcher = DocumentSearcher(db)
    results = searcher.search(query, type_filter=type_filter)

    print(f"\n===== 檢索結果：共找到 {len(results)} 份文件 =====")
    for idx, r in enumerate(results, start=1):
        print(f"\n[{idx}] {r.filename} ({r.file_type.upper()}, {r.file_size} bytes)")
        print(f"    路徑: {r.path}")
        print(f"    命中區塊數: {r.total_matches}")
        for seg in r.segments:
            print(f"    - [{seg.segment_type} {seg.segment_id}]")
            for snip in seg.snippets:
                # Strip HTML tags for clean terminal printing
                import re
                clean_snip = re.sub(r'<[^>]+>', '', snip)
                print(f"      {clean_snip}")
    print("\n================================================")
    db.close()


def main():
    parser = argparse.ArgumentParser(description="本機多格式文件內文關鍵字檢索系統")
    parser.add_argument("--dir", help="指定要檢索的資料夾目錄路徑 (CLI 模式)")
    parser.add_argument("--search", help="搜尋關鍵字 (CLI 模式)")
    parser.add_argument("--type", default="all", help="格式過濾 (all, pdf, word, excel, ppt, text)")

    args = parser.parse_args()

    if args.dir and args.search:
        run_cli_mode(args.dir, args.search, args.type)
    else:
        # Launch PySide6 GUI
        from ui.app import run_app
        run_app()


if __name__ == "__main__":
    main()
