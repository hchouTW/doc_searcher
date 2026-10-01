# Purpose: Optional real local multilingual dense embeddings over original passage offsets.
# Behavior: Cache a local E5 model; atomically publish vectors only for an unchanged revision.
# Usage: Install .[semantic], set DOC_SEARCHER_EMBEDDING_MODEL to a local multilingual E5 directory.
import hashlib
import importlib
import heapq
import json
import os
import threading
from array import array
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from doc_searcher.search.chunks import chunk_text
from doc_searcher.indexing.service import _write_lock


class Encoder(Protocol):
    model_id: str
    max_tokens: int

    def encode(self, texts: list[str], query: bool = False) -> list[list[float]]: ...
    def token_count(self, text: str) -> int: ...


class LocalEncoder:
    def __init__(self, path):
        if not path or not os.path.isdir(path):
            raise ValueError(
                "Configure a local multilingual E5 model directory with DOC_SEARCHER_EMBEDDING_MODEL."
            )
        try:
            SentenceTransformer = importlib.import_module(
                "sentence_transformers"
            ).SentenceTransformer
        except ImportError as exc:
            raise ValueError(
                "Install doc-searcher[semantic] for local dense model search."
            ) from exc
        self._guard = threading.Lock()
        self.model = SentenceTransformer(
            path, local_files_only=True, device="cpu", trust_remote_code=False
        )
        self.max_tokens = min(512, self.model.max_seq_length)
        # Include the resolved path and file metadata so replacing a model invalidates vectors.
        files = [
            (str(p.relative_to(path)), p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(Path(path).rglob("*"))
            if p.is_file() and ".cache" not in p.parts
        ]
        self.model_id = hashlib.sha256(
            json.dumps([str(Path(path).resolve()), files]).encode()
        ).hexdigest()

    def token_count(self, text):
        return len(self.model.tokenizer("passage: " + text, truncation=False)["input_ids"])

    def encode(self, texts, query=False):
        prefix = "query: " if query else "passage: "
        with self._guard:
            return self.model.encode(
                [prefix + text for text in texts],
                batch_size=16,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist()


@lru_cache(maxsize=2)
def _local_encoder(path):
    return LocalEncoder(path)


class SemanticIndex:
    def __init__(self, db, encoder=None):
        self.db = db
        self.encoder = encoder or _local_encoder(os.environ.get("DOC_SEARCHER_EMBEDDING_MODEL", ""))

    def ready(self):
        row = (
            self.db.get_connection()
            .execute("SELECT * FROM semantic_state WHERE singleton=1")
            .fetchone()
        )
        return bool(
            row
            and row["model_id"] == self.encoder.model_id
            and row["revision"] == self.db.revision()
        )

    def rebuild(self, cancel_check=None):
        conn = self.db.get_connection()
        if conn.in_transaction:
            raise ValueError("Build semantic vectors outside a search transaction.")
        with conn:
            conn.execute("BEGIN")
            revision = self.db.revision()
            segments = [
                dict(row)
                for row in conn.execute("SELECT id, content FROM doc_segments ORDER BY id")
            ]
            state = conn.execute("SELECT * FROM semantic_state WHERE singleton=1").fetchone()
            reusable = {}
            if state and state["model_id"] == self.encoder.model_id:
                for row in conn.execute("SELECT * FROM semantic_chunks"):
                    reusable.setdefault(row["segment_row_id"], []).append(dict(row))
        # No database writer lock while the model runs. Existing keyword data is searchable.
        records, vectors = [], []
        for segment in segments:
            if segment["id"] in reusable:
                for row in reusable[segment["id"]]:
                    values = array("f")
                    values.frombytes(row["vector"])
                    records.append((segment["id"], row["start"], row["end"]))
                    vectors.append(list(values))
                continue
            chunks = chunk_text(
                segment["content"],
                token_count=self.encoder.token_count,
                max_tokens=self.encoder.max_tokens,
            )
            for start in range(0, len(chunks), 16):
                if cancel_check and cancel_check():
                    raise ValueError("Semantic indexing cancelled.")
                batch = chunks[start : start + 16]
                encoded = self.encoder.encode([chunk.text for chunk in batch])
                if len(encoded) != len(batch):
                    raise ValueError("Embedding model returned an invalid batch size.")
                records.extend((segment["id"], chunk.start, chunk.end) for chunk in batch)
                vectors.extend(encoded)
        dimension = len(vectors[0]) if vectors else 0
        if any(len(v) != dimension for v in vectors):
            raise ValueError("Embedding dimensions changed during indexing.")
        if cancel_check and cancel_check():
            raise ValueError("Semantic indexing cancelled.")
        with _write_lock(self.db.db_path), conn:
            conn.execute("BEGIN IMMEDIATE")
            if revision != self.db.revision():
                raise ValueError("Source index changed during semantic indexing; retry.")
            conn.execute("DELETE FROM semantic_chunks")
            conn.executemany(
                "INSERT INTO semantic_chunks(segment_row_id,start,end,vector) VALUES (?,?,?,?)",
                [
                    (*record, array("f", vector).tobytes())
                    for record, vector in zip(records, vectors, strict=True)
                ],
            )
            conn.execute(
                "INSERT OR REPLACE INTO semantic_state VALUES (1,?,?,?)",
                (self.encoder.model_id, dimension, revision),
            )
        return len(records)

    def search(self, query, filter_clause="", filter_params=(), *, limit=1000, cancel_check=None):
        if not self.ready():
            raise ValueError("Semantic index is pending or stale; rebuild vectors first.")
        vector = self.encoder.encode([query], query=True)[0]
        state = (
            self.db.get_connection()
            .execute("SELECT dimensions FROM semantic_state WHERE singleton=1")
            .fetchone()
        )
        if state["dimensions"] and len(vector) != state["dimensions"]:
            raise ValueError("Query model dimensions differ from stored vectors; rebuild required.")
        norm = sum(value * value for value in vector) ** 0.5
        if not norm:
            raise ValueError("Query model returned an empty vector.")
        heap = []
        sql = (
            "SELECT c.*, s.doc_id, s.segment_id, s.segment_type, s.content, s.sources FROM semantic_chunks c "
            "JOIN doc_segments s ON s.id=c.segment_row_id JOIN documents d ON d.id=s.doc_id "
            "WHERE 1=1 " + filter_clause
        )
        for row in self.db.get_connection().execute(sql, filter_params):
            if cancel_check and cancel_check():
                raise ValueError("Semantic search cancelled.")
            values = array("f")
            values.frombytes(row["vector"])
            denominator = norm * sum(value * value for value in values) ** 0.5
            score = (
                sum(a * b for a, b in zip(vector, values, strict=True)) / denominator
                if denominator
                else 0
            )
            item = (score, -row["id"], dict(row))
            if len(heap) < limit:
                heapq.heappush(heap, item)
            elif item[:2] > heap[0][:2]:
                heapq.heapreplace(heap, item)
        return [(score, row) for score, _, row in sorted(heap, reverse=True)]


class SemanticMaintainer:
    """Maintain optional vectors on a separate worker; never hold up native file events."""

    def __init__(self, db):
        self.db = db
        self._stop = threading.Event()
        self._state = {"state": "pending", "error": None}
        self._thread = threading.Thread(target=self._run, name="semantic-index-worker", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join()

    def status(self):
        return dict(self._state)

    def _run(self):
        try:
            index = SemanticIndex(self.db)
            while not self._stop.is_set():
                if not index.ready():
                    self._state = {"state": "indexing", "error": None}
                    try:
                        count = index.rebuild(self._stop.is_set)
                        self._state = {"state": "ready", "error": None, "chunks": count}
                    except ValueError as exc:
                        self._state = {"state": "pending", "error": str(exc)}
                self._stop.wait(0.5)
        except Exception as exc:
            self._state = {"state": "failed", "error": str(exc)}
        finally:
            self.db.close()
