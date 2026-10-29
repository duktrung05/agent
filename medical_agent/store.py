"""SQLite là checkpoint gốc; state.json/trace.jsonl là bản xuất để học và debug."""

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

from .core import utc_now


class Store:
    def __init__(self, directory="outputs"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "agent.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, indexed_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS document_search USING fts5(
                source_id UNINDEXED, title, content, tokenize='unicode61 remove_diacritics 2'
            );
            CREATE TABLE IF NOT EXISTS response_cache (
                key TEXT PRIMARY KEY, saved_at TEXT NOT NULL, payload TEXT NOT NULL
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def checkpoint(self, state, kind, payload=None):
        state["updated_at"] = utc_now()
        encoded = json.dumps(state, ensure_ascii=False)
        with self.db:
            self.db.execute("INSERT INTO runs VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at", (state["id"], encoded, state["updated_at"]))
            self.db.execute("INSERT INTO events(run_id,at,kind,payload) VALUES(?,?,?,?)", (state["id"], state["updated_at"], kind, json.dumps(payload or {}, ensure_ascii=False)))
        self.export(state)

    def load(self, run_id):
        row = self.db.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Không có run {run_id}.")
        return json.loads(row[0])

    def recent(self, limit=10):
        return [json.loads(row[0]) for row in self.db.execute("SELECT state FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,))]

    def events(self, run_id):
        return [{"seq": r["seq"], "at": r["at"], "kind": r["kind"], "payload": json.loads(r["payload"])} for r in self.db.execute("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,))]

    def run_directory(self, run_id):
        if not re.fullmatch(r"[a-f0-9]{12}", run_id):
            raise ValueError("Run ID không hợp lệ.")
        path = self.directory / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def export(self, state):
        path = self.run_directory(state["id"])
        temp = path / "state.json.tmp"
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path / "state.json")
        events = self.events(state["id"])
        temp = path / "trace.jsonl.tmp"
        temp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        temp.replace(path / "trace.jsonl")

    def put_document(self, document):
        with self.db:
            return self._put_document(document)

    def _put_document(self, document):
        document = dict(document)
        text = document.get("content") or ""
        document["content_hash"] = hashlib.sha256(text.encode()).hexdigest()
        self.db.execute("INSERT INTO documents VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,indexed_at=excluded.indexed_at", (document["id"], json.dumps(document, ensure_ascii=False), utc_now()))
        self.db.execute("DELETE FROM document_search WHERE source_id=?", (document["id"],))
        if text.strip():
            self.db.execute("INSERT INTO document_search VALUES(?,?,?)", (document["id"], document.get("title") or "", text))
        return document

    def import_documents(self, path, documents):
        """Replace the file's local chunks atomically; existing run snapshots stay intact."""
        keep = {doc["id"] for doc in documents}
        stale = []
        for row in self.db.execute("SELECT id,payload FROM documents"):
            doc = json.loads(row["payload"])
            if doc.get("origin") == "local" and doc.get("path") == str(path) and doc["id"] not in keep:
                stale.append(doc["id"])
        with self.db:
            for source_id in stale:
                self.db.execute("DELETE FROM document_search WHERE source_id=?", (source_id,))
                self.db.execute("DELETE FROM documents WHERE id=?", (source_id,))
            for doc in documents:
                self._put_document(doc)

    def write_report(self, state):
        from .report import render_report
        path = self.run_directory(state["id"]) / "report.md"
        temp = path.with_suffix(".md.tmp")
        temp.write_text(render_report(state), encoding="utf-8")
        temp.replace(path)

    def get_document(self, source_id):
        row = self.db.execute("SELECT payload FROM documents WHERE id=?", (source_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def document_count(self):
        return self.db.execute("SELECT count(*) FROM document_search").fetchone()[0]

    def has_unread_documents(self, excluded_ids):
        ids = list(excluded_ids)
        sql = "SELECT 1 FROM document_search"
        if ids:
            sql += " WHERE source_id NOT IN (" + ",".join("?" for _ in ids) + ")"
        return self.db.execute(sql + " LIMIT 1", ids).fetchone() is not None

    def search_documents(self, query, limit=5):
        words = re.findall(r"[^\W_]+", unicodedata.normalize("NFC", query).lower(), re.UNICODE)
        stop = {"and", "or", "not", "the", "a", "an", "of", "và", "về", "của", "là"}
        words = list(dict.fromkeys(w for w in words if w not in stop))[:24]
        if not words:
            return []
        expression = " OR ".join('"' + word + '"' for word in words)
        rows = self.db.execute("SELECT source_id,bm25(document_search) AS score FROM document_search WHERE document_search MATCH ? ORDER BY score LIMIT ?", (expression, limit)).fetchall()
        result = []
        for row in rows:
            doc = self.get_document(row["source_id"])
            result.append({"id": doc["id"], "title": doc.get("title"), "year": doc.get("year"), "origin": doc.get("origin"), "fetched_at": doc.get("fetched_at"), "excerpt": doc["content"][:240], "retrieval_score": row["score"]})
        return result

    def cache_get(self, key, max_age_seconds=86400):
        from datetime import datetime, timezone
        row = self.db.execute("SELECT * FROM response_cache WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["saved_at"])).total_seconds()
        if age > max_age_seconds:
            return None
        return json.loads(row["payload"])

    def cache_put(self, key, payload):
        with self.db:
            self.db.execute("INSERT INTO response_cache VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET saved_at=excluded.saved_at,payload=excluded.payload", (key, utc_now(), json.dumps(payload, ensure_ascii=False)))
