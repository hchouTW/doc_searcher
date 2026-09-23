# Purpose: Build synthetic v1.2.0-era index.db and config.json files for migration tests.
# What the code does:
#   - write_legacy_db(path, with_ctime) recreates the v1.2.0 schema byte-for-byte in SQL
#     (user_version 0), optionally without the later-added ctime column, and inserts two
#     documents with segments and FTS rows.
#   - write_legacy_config(path, directories) writes a v1.2.0 config.json.
# Usage notes, dependencies, or assumptions:
#   - All paths and text are synthetic (/legacy/...); no personal data or real documents.
#   - Built in code rather than committed as binaries so the schema stays reviewable.

import json
import sqlite3
from pathlib import Path
from typing import List

LEGACY_DOCUMENTS = [
    # (path, file_type, size, mtime, [(segment_id, segment_type, content, tokenized)])
    (
        "/legacy/root_a/report.txt",
        "txt",
        120,
        1_700_000_000.0,
        [("1", "page", "legacy alpha report", "legacy alpha report")],
    ),
    (
        "/legacy/root_b/notes.md",
        "md",
        80,
        1_700_000_100.0,
        [
            ("1", "page", "legacy beta notes", "legacy beta notes"),
            ("2", "page", "second segment", "second segment"),
        ],
    ),
]


def write_legacy_db(path: Path, with_ctime: bool = True) -> None:
    conn = sqlite3.connect(str(path))
    ctime_column = "ctime REAL NOT NULL DEFAULT 0," if with_ctime else ""
    conn.executescript(f"""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            {ctime_column}
            indexed_at REAL NOT NULL,
            total_segments INTEGER DEFAULT 0,
            error TEXT
        );
        CREATE TABLE doc_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER NOT NULL,
            segment_id TEXT NOT NULL,
            segment_type TEXT NOT NULL,
            content TEXT NOT NULL,
            FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
        );
        CREATE INDEX idx_doc_segments_doc_id ON doc_segments(doc_id);
        CREATE VIRTUAL TABLE doc_fts USING fts5(
            doc_id UNINDEXED, segment_id UNINDEXED, segment_type UNINDEXED,
            content, tokenized_content, tokenize='unicode61'
        );
    """)
    for doc_path, file_type, size, mtime, segments in LEGACY_DOCUMENTS:
        columns = "path, filename, file_type, file_size, mtime, indexed_at, total_segments"
        values = [
            doc_path,
            doc_path.rsplit("/", 1)[1],
            file_type,
            size,
            mtime,
            mtime,
            len(segments),
        ]
        if with_ctime:
            columns += ", ctime"
            values.append(mtime)
        cursor = conn.execute(
            f"INSERT INTO documents ({columns}) VALUES ({', '.join('?' * len(values))})", values
        )
        doc_id = cursor.lastrowid
        for segment_id, segment_type, content, tokenized in segments:
            conn.execute(
                "INSERT INTO doc_segments (doc_id, segment_id, segment_type, content) VALUES (?, ?, ?, ?)",
                (doc_id, segment_id, segment_type, content),
            )
            conn.execute(
                "INSERT INTO doc_fts VALUES (?, ?, ?, ?, ?)",
                (str(doc_id), segment_id, segment_type, content, tokenized),
            )
    conn.commit()
    conn.close()


def write_legacy_config(path: Path, directories: List[str]) -> dict:
    data = {
        "directories": directories,
        "include_subdirectories": True,
        "enabled_extensions": [".pdf", ".docx", ".txt", ".md"],
        "max_snippet_chars": 80,
        "db_path": str(path.parent / "index.db"),
        "theme": "dark",
        "language": "en-US",
        "search_debounce_ms": 450,
        "last_updated_at": 1_700_000_200.0,
        "exclude_patterns": [".git", "build"],
        "search_filters": {"file_type": "pdf", "regex": True, "min_size": 1.5},
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
