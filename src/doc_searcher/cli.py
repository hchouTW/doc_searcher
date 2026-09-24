# Purpose: Main GUI/CLI entry point for Document Searcher (console script `doc-searcher`).
# What the code does:
#   - Supports both GUI mode (default) and CLI mode for automation/testing.
#   - In CLI mode: indexes one directory through indexing.service.IndexingService, scoped so only
#     that directory's entries are reconciled (other roots are never touched), then searches.
#   - In GUI mode: launches PySide6 desktop interface.
# Usage notes, dependencies, or assumptions:
#   - GUI: doc-searcher   (or python -m doc_searcher)
#   - CLI: doc-searcher --dir /path/to/folder --search "關鍵字"
#   - Also: doc-searcher --help | --version | --self-check [--report FILE] (none starts the GUI;
#     see doc_searcher.selfcheck).
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
    from doc_searcher.indexing.service import IndexingService, IndexRequest
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
        # Scope = the requested root: entries owned by other roots are never reconciled.
        request = IndexRequest(roots=[abs_dir], scope=[abs_dir])
        try:
            stats = IndexingService(db, scanner).run(request)
        except StorageError as exc:
            print(f"[!] {exc}", file=sys.stderr)
            return EXIT_DATA
        print(f"[*] 找到 {stats['scanned']} 個支援的文件檔案。")
        if stats["indexed"] or stats["deleted"] or stats["failed"]:
            summary = {key: stats[key] for key in ("indexed", "deleted", "failed", "cancelled")}
            print(f"[✓] 索引更新完成：{summary}")
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
        "--self-check",
        action="store_true",
        help="檢查執行環境（解析器、FTS5、資源檔）後結束，不開啟視窗",
    )
    parser.add_argument("--report", metavar="FILE", help="將 --self-check 結果另存至檔案")
    parser.add_argument(
        "--type",
        default="all",
        type=str.lower,
        choices=TYPE_FILTERS,
        help="格式過濾 (all, pdf, word, excel, ppt, text)",
    )

    args = parser.parse_args(argv)

    if args.self_check:
        from doc_searcher.selfcheck import run_self_check

        return run_self_check(args.report)
    if args.report:
        parser.error("--report 只能與 --self-check 一起使用")

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
