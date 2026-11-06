"""Persistent metadata. Audio files stay in the existing uploads/outputs volumes."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


def decode(payload: str):
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise RuntimeError("服务元数据损坏，请从 SQLite 备份恢复") from error


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS voices (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
                favorite INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, table: str, item_id: str):
        with self.connection() as db:
            if table == "voices":
                row = db.execute("SELECT data FROM voices WHERE id=?", (item_id,)).fetchone()
            elif table == "jobs":
                row = db.execute("SELECT data FROM jobs WHERE id=?", (item_id,)).fetchone()
            else:
                raise ValueError("unknown table")
        return decode(row[0]) if row else None

    def put(self, table: str, item: dict):
        payload = json.dumps(item, ensure_ascii=False, allow_nan=False)
        with self.connection() as db:
            if table == "voices":
                db.execute("INSERT OR REPLACE INTO voices VALUES (?,?)", (item["id"], payload))
            elif table == "jobs":
                db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?)", (
                    item["id"], item["status"], item["created_at"], int(item["favorite"]), payload))
            else:
                raise ValueError("unknown table")

    def delete(self, table: str, item_id: str):
        with self.connection() as db:
            if table == "voices":
                db.execute("DELETE FROM voices WHERE id=?", (item_id,))
            elif table == "jobs":
                db.execute("DELETE FROM jobs WHERE id=?", (item_id,))
            else:
                raise ValueError("unknown table")

    def voices(self):
        with self.connection() as db:
            return [decode(row[0]) for row in db.execute("SELECT data FROM voices")]

    def jobs(self, *, status=None, query="", favorite=False, limit=100):
        with self.connection() as db:
            rows = db.execute("""SELECT data FROM jobs
                WHERE (? IS NULL OR status=?) AND (?=0 OR favorite=1)
                ORDER BY created_at DESC""", (status, status, int(favorite)))
            # Search the actual text/title/voice rather than serialized parameter names.
            items = [decode(row[0]) for row in rows]
        if query:
            term = query.casefold()
            items = [x for x in items if term in " ".join((x["text"], x["title"], x["voice_name"])).casefold()]
        return items[:limit], len(items)

    def claim_next(self, started_at):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            item = decode(row[0])
            item.update(status="running", started_at=started_at)
            db.execute("UPDATE jobs SET status='running', data=? WHERE id=? AND status='queued'",
                       (json.dumps(item, ensure_ascii=False, allow_nan=False), item["id"]))
        return item

    def counts(self):
        with self.connection() as db:
            statuses = dict(db.execute("SELECT status, count(*) FROM jobs GROUP BY status"))
            voices = db.execute("SELECT count(*) FROM voices").fetchone()[0]
        return {"queued": statuses.get("queued", 0), "running": statuses.get("running", 0)}, {
            "voices": voices, "jobs": sum(statuses.values())}
