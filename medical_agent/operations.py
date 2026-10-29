"""Consistent SQLite backups and non-destructive restoration for the local CLI."""

import json
from contextlib import closing
from pathlib import Path
import sqlite3

from .core import utc_now
from .corpus import file_sha256, write_json_new
from .store import Store


def backup_store(store, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    database = destination / "agent.sqlite3"
    with closing(sqlite3.connect(database)) as target:
        store.db.backup(target)
    # Exports are rebuilt from the backup snapshot, not the possibly changing live DB.
    backup = Store(destination)
    try:
        for row in backup.db.execute("SELECT state FROM runs").fetchall():
            state = json.loads(row["state"])
            backup.export(state)
            backup.write_report(state)
        counts = {"runs": backup.db.execute("SELECT count(*) FROM runs").fetchone()[0], "documents": backup.document_count()}
    finally:
        backup.close()
    manifest = {"schema_version": "1.0", "created_at": utc_now(), "database": "agent.sqlite3",
                "sha256": file_sha256(database), "sqlite_version": sqlite3.sqlite_version, **counts}
    write_json_new(destination / "backup.json", manifest)
    return manifest


def restore_store(source, destination):
    source, destination = Path(source).resolve(), Path(destination)
    if destination.exists():
        raise ValueError("Thư mục khôi phục phải chưa tồn tại; không ghi đè runtime đang dùng.")
    manifest = json.loads((source / "backup.json").read_text(encoding="utf-8"))
    database = source / "agent.sqlite3"
    if manifest.get("database") != "agent.sqlite3" or manifest.get("sha256") != file_sha256(database):
        raise ValueError("Bản sao lưu không khớp hash; không khôi phục.")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as backup:
        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity_check không đạt.")
        destination.mkdir(parents=True)
        with closing(sqlite3.connect(destination / "agent.sqlite3")) as target:
            backup.backup(target)
    restored = Store(destination)
    try:
        for row in restored.db.execute("SELECT state FROM runs").fetchall():
            state = json.loads(row["state"])
            restored.export(state)
            restored.write_report(state)
    finally:
        restored.close()
    return manifest
