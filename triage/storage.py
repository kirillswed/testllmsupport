import json
import sqlite3
from pathlib import Path

from .clients import utc_now


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                content_hash TEXT PRIMARY KEY,
                first_filename TEXT NOT NULL,
                source_text TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                report_json TEXT
            );
            CREATE TABLE IF NOT EXISTS run_items (
                run_id TEXT NOT NULL REFERENCES runs(id),
                filename TEXT NOT NULL,
                content_hash TEXT REFERENCES messages(content_hash),
                disposition TEXT NOT NULL,
                result_json TEXT NOT NULL,
                PRIMARY KEY (run_id, filename)
            );
            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(id),
                content_hash TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notifications (
                content_hash TEXT NOT NULL REFERENCES messages(content_hash),
                notification_key TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (content_hash, notification_key)
            );
        """)

    def close(self):
        self.db.close()

    def start_run(self, run_id: str):
        with self.db:
            self.db.execute("INSERT INTO runs(id, started_at) VALUES (?, ?)", (run_id, utc_now()))

    def finish_run(self, run_id: str, report: dict):
        with self.db:
            self.db.execute("UPDATE runs SET finished_at=?, report_json=? WHERE id=?",
                            (utc_now(), json.dumps(report, ensure_ascii=False), run_id))

    def get(self, digest: str) -> dict | None:
        row = self.db.execute("SELECT result_json FROM messages WHERE content_hash=?", (digest,)).fetchone()
        return json.loads(row["result_json"]) if row else None

    def save(self, digest: str, filename: str, text: str, result: dict):
        now = utc_now()
        with self.db:
            self.db.execute("""
                INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    result_json=excluded.result_json, updated_at=excluded.updated_at
            """, (digest, filename, text, json.dumps(result, ensure_ascii=False), now, now))

    def add_item(self, run_id: str, filename: str, digest: str | None, disposition: str, result: dict):
        with self.db:
            self.db.execute("INSERT INTO run_items VALUES (?, ?, ?, ?, ?)",
                            (run_id, filename, digest, disposition, json.dumps(result, ensure_ascii=False)))

    def add_call(self, run_id: str, record: dict):
        with self.db:
            self.db.execute("INSERT INTO llm_calls(run_id, content_hash, record_json) VALUES (?, ?, ?)",
                            (run_id, record["content_hash"], json.dumps(record)))

    def notify_once(self, digest: str, key: str, message: str, emit=print) -> bool:
        existing = self.db.execute(
            "SELECT 1 FROM notifications WHERE content_hash=? AND notification_key=?", (digest, key)
        ).fetchone()
        if existing:
            return False
        emit(message)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO notifications VALUES (?, ?, ?)", (digest, key, utc_now()))
        return True

