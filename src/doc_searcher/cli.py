# Purpose: Main GUI/CLI entry point for Document Searcher (console script `doc-searcher`).
# What the code does:
#   - Supports both GUI mode (default) and CLI mode for automation/testing.
#   - In CLI mode: scans one directory, reconciles only that directory's index entries (other
#     roots are never touched), runs a search, and prints results.
#   - In GUI mode: launches PySide6 desktop interface.
# Usage notes, dependencies, or assumptions:
#   - GUI: doc-searcher   (or python -m doc_searcher)
#   - CLI: doc-searcher --dir /path/to/folder --search "關鍵字"
#   - Also: doc-searcher --help | --version (neither starts the GUI)
#   - CLI exit codes: 0 success, 1 directory unavailable (index left unchanged), 2 usage error,
#     3 data folder or index.db unusable (no writable folder, locked/corrupt/too-new index).

import sys
import os
import argparse
import re

EXIT_OK = 0
EXIT_UNAVAILABLE = 1
EXIT_DATA = 3
TYPE_FILTERS = ("all", "pdf", "word", "doc", "excel", "xls", "ppt", "powerpoint", "text")


def run_cli_mode(folder: str, query: str, type_filter: str = "all") -> int:
    """Run headless search via terminal for quick testing or scripts; returns an exit code."""
    from doc_searcher.config import AppConfig, ConfigError
    from doc_searcher.storage.database import Database
    from doc_searcher.storage.errors import StorageError
    from doc_searcher.indexing.scanner import FileScanner
    from doc_searcher.indexing.indexer import DocumentIndexer
    from doc_searcher.search.searcher import DocumentSearcher

    abs_dir = os.path.abspath(folder)
    print(f"[*] 掃描目錄：{abs_dir}")

    try:
        config = AppConfig()
    except ConfigError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return EXIT_DATA
    scanner = FileScanner()

    available_directories, _ = scanner.partition_directories([abs_dir])
    if not available_directories:
        print(f"[!] 檢索目錄目前無法存取：{abs_dir}；既有索引已保留，未進行變更。", file=sys.stderr)
        return EXIT_UNAVAILABLE

    try:
        db = Database(config.db_path)
    except StorageError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return EXIT_DATA
    try:
        indexer = DocumentIndexer(db)
        scan_errors = []
        files = scanner.scan_directories(available_directories, error_callback=scan_errors.append)
        print(f"[*] 找到 {len(files)} 個支援的文件檔案，開始建立/更新索引...")

        # Only reconcile entries under the requested root so other roots keep their records.
        current_indexed = {
            path: value
            for path, value in db.get_all_indexed_paths().items()
            if scanner.is_path_within_directory(path, abs_dir)
        }
        to_index, to_delete = scanner.calculate_changes(
            files,
            current_indexed,
            preserved_directories=scanner.normalize_directories(scan_errors),
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
                    clean_snip = re.sub(r"<[^>]+>", "", snip)
                    print(f"      {clean_snip}")
        print("\n================================================")
        return EXIT_OK
    finally:
        db.close()


def main(argv=None) -> int:
    from doc_searcher.version import APP_VERSION

    parser = argparse.ArgumentParser(description="本機多格式文件內文關鍵字檢索系統")
    parser.add_argument("--version", action="version", version=f"DocSearcher {APP_VERSION}")
    parser.add_argument("--dir", help="指定要檢索的資料夾目錄路徑 (CLI 模式)")
    parser.add_argument("--search", help="搜尋關鍵字 (CLI 模式)")
    parser.add_argument(
        "--type",
        default="all",
        type=str.lower,
        choices=TYPE_FILTERS,
        help="格式過濾 (all, pdf, word, excel, ppt, text)",
    )

    args = parser.parse_args(argv)

    if args.dir is not None or args.search is not None:
        if not (args.dir and args.search):
            parser.error("CLI 模式需要同時指定 --dir 與 --search")
        return run_cli_mode(args.dir, args.search, args.type)

    # Launch PySide6 GUI
    from doc_searcher.desktop.app import run_app

    run_app()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
