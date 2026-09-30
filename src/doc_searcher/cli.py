# Purpose: Main GUI/CLI entry point for Document Searcher (console script `doc-searcher`).
# What the code does:
#   - Supports both GUI mode (default) and CLI mode for automation/testing.
#   - In CLI mode: indexes one directory through indexing.service.IndexingService, scoped so only
#     that directory's entries are reconciled (other roots are never touched), then searches.
#   - In GUI mode: launches PySide6 desktop interface.
# Usage notes, dependencies, or assumptions:
#   - GUI: doc-searcher   (or python -m doc_searcher)
#   - CLI: doc-searcher --dir /path/to/folder --search "關鍵字"
#   - CLI stdout carries only search results; progress and diagnostics go to stderr.
#   - Also: doc-searcher --help | --version | --self-check [--report FILE] (none starts the GUI;
#     see doc_searcher.selfcheck).
#   - CLI exit codes: 0 success, 1 directory unavailable (index left unchanged), 2 usage error,
#     3 data folder or index.db unusable (no writable folder, locked/corrupt/too-new index).

import json
from dataclasses import asdict
import sys
import os
import argparse
import re

from doc_searcher.diagnostics import configure_logging

EXIT_OK = 0
EXIT_UNAVAILABLE = 1
EXIT_DATA = 3
TYPE_FILTERS = ("all", "pdf", "word", "doc", "excel", "xls", "ppt", "powerpoint", "text")


def run_cli_mode(folder: str, query: str, type_filter: str = "all", *, limit=200, cursor=None, locations=None, context=None, json_output=False, offset=0, revision=None, regex=False, match_case=False, whole_word=False) -> int:
    """Run headless search via terminal for quick testing or scripts; returns an exit code."""
    from doc_searcher.config import AppConfig, ConfigError
    from doc_searcher.storage.database import Database
    from doc_searcher.storage.errors import StorageError
    from doc_searcher.indexing.scanner import FileScanner
    from doc_searcher.indexing.service import IndexingService, IndexRequest
    from doc_searcher.search.searcher import DocumentSearcher

    abs_dir = os.path.abspath(folder)
    print(f"[*] 掃描目錄：{abs_dir}", file=sys.stderr)

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
        print(f"[*] 找到 {stats['scanned']} 個支援的文件檔案。", file=sys.stderr)
        if stats["indexed"] or stats["deleted"] or stats["failed"]:
            summary = {key: stats[key] for key in ("indexed", "deleted", "failed", "cancelled")}
            print(f"[✓] 索引更新完成：{summary}", file=sys.stderr)
        else:
            print("[✓] 索引已是最新狀態。", file=sys.stderr)

        print(f"[*] 執行檢索關鍵字：'{query}' (篩選: {type_filter})", file=sys.stderr)
        searcher = DocumentSearcher(db)
        try:
            if locations is not None:
                print(json.dumps(asdict(searcher.match_locations(query, locations, revision=db.revision() if revision is None else revision, offset=offset, regex=regex, match_case=match_case, whole_word=whole_word)), ensure_ascii=False))
                return EXIT_OK
            if context is not None:
                print(json.dumps(searcher.match_context(json.loads(context)), ensure_ascii=False))
                return EXIT_OK
            page = searcher.search_page(query, type_filter=type_filter, limit=limit, cursor=cursor, regex=regex, match_case=match_case, whole_word=whole_word)
        except (ValueError, LookupError) as exc:
            print(f"[!] {exc}", file=sys.stderr)
            return 2
        results = page.items
        if json_output:
            print(json.dumps(asdict(page), ensure_ascii=False))
            return EXIT_OK
        if page.has_more:
            print(f"[*] 已載入 {len(results)} 份文件，尚有更多結果。 --cursor {page.next_cursor}", file=sys.stderr)

        print(f"\n===== 檢索結果：共找到 {len(results)} 份文件 =====")
        for idx, r in enumerate(results, start=1):
            print(f"\n[{idx}] {r.filename} ({r.file_type.upper()}, {r.file_size} bytes)")
            print(f"    路徑: {r.path}")
            print(f"    命中次數: {r.total_matches}；命中區塊數: {r.segment_count}")
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

    parser.add_argument("--limit", type=int, default=200, help="Maximum documents per page")
    parser.add_argument("--cursor", help="Continuation cursor from a previous search")
    parser.add_argument("--locations", type=int, metavar="DOC_ID", help="Return occurrence locations as JSON")
    parser.add_argument("--context", metavar="LOCATION_JSON", help="Read bounded occurrence context")
    parser.add_argument("--json", action="store_true", help="Return search page as JSON")
    parser.add_argument("--offset", type=int, default=0, help="Occurrence-page offset")
    parser.add_argument("--revision", type=int, help="Revision returned by search")
    parser.add_argument("--regex", action="store_true", help="Bounded regular-expression search")
    parser.add_argument("--match-case", action="store_true", help="Match original letter case")
    parser.add_argument("--whole-word", action="store_true", help="Match whole words")
    args = parser.parse_args(argv)
    configure_logging()

    if args.self_check:
        from doc_searcher.selfcheck import run_self_check

        return run_self_check(args.report)
    if args.report:
        parser.error("--report 只能與 --self-check 一起使用")

    if args.dir is not None or args.search is not None:
        if not (args.dir and args.search):
            parser.error("CLI 模式需要同時指定 --dir 與 --search")
        if args.cursor or args.locations is not None or args.context or args.json or args.limit != 200 or args.regex or args.match_case or args.whole_word or args.offset or args.revision is not None:
            return run_cli_mode(args.dir, args.search, args.type, limit=args.limit, cursor=args.cursor,
                                locations=args.locations, context=args.context, json_output=args.json, offset=args.offset, revision=args.revision,
                                regex=args.regex, match_case=args.match_case, whole_word=args.whole_word)
        return run_cli_mode(args.dir, args.search, args.type)

    # Launch PySide6 GUI
    from doc_searcher.desktop.app import run_app

    run_app()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
