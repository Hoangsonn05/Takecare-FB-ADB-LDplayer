from __future__ import annotations

from pathlib import Path
from typing import Optional
import sqlite3
import threading
import time

from core.events import AutomationEvent
from core.paths import app_path


class RunDatabase:
    def __init__(self, db_path: str | Path):
        self.path = app_path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    profile_name TEXT,
                    total_devices INTEGER NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    device_index INTEGER NOT NULL,
                    serial TEXT,
                    status TEXT,
                    runtime_sec REAL DEFAULT 0,
                    posts_processed INTEGER DEFAULT 0,
                    swipe_count INTEGER DEFAULT 0,
                    reaction_count INTEGER DEFAULT 0,
                    screenshot_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    UNIQUE(run_id, device_index),
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER,
                    device_index INTEGER,
                    ts REAL NOT NULL,
                    level TEXT NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT,
                    message TEXT,
                    runtime_sec REAL,
                    posts_processed INTEGER,
                    swipe_count INTEGER,
                    reaction_count INTEGER,
                    screenshot_count INTEGER,
                    error_count INTEGER
                );
                """
            )
            self._ensure_column(conn, "device_runs", "posts_processed", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "device_runs", "reaction_count", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "events", "posts_processed", "INTEGER")
            self._ensure_column(conn, "events", "reaction_count", "INTEGER")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def begin_run(self, profile_name: str, total_devices: int) -> int:
        with self.lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs(started_at, profile_name, total_devices, status) VALUES (?, ?, ?, ?)",
                (time.time(), profile_name, total_devices, "RUNNING"),
            )
            return int(cur.lastrowid)

    def end_run(self, run_id: int, status: str = "DONE") -> None:
        with self.lock, self._connect() as conn:
            conn.execute("UPDATE runs SET ended_at=?, status=? WHERE id=?", (time.time(), status, run_id))

    def upsert_device(self, run_id: int, index: int, serial: str, event: AutomationEvent) -> None:
        with self.lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO device_runs(run_id, device_index, serial, status, runtime_sec, posts_processed, swipe_count, reaction_count, screenshot_count, error_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, device_index) DO UPDATE SET
                    status=excluded.status,
                    runtime_sec=excluded.runtime_sec,
                    posts_processed=excluded.posts_processed,
                    swipe_count=excluded.swipe_count,
                    reaction_count=excluded.reaction_count,
                    screenshot_count=excluded.screenshot_count,
                    error_count=excluded.error_count
                """,
                (
                    run_id,
                    index,
                    serial,
                    event.status,
                    event.runtime_sec,
                    event.posts_processed,
                    event.swipe_count,
                    event.reaction_count,
                    event.screenshot_count,
                    event.error_count,
                ),
            )

    def add_event(self, run_id: Optional[int], event: AutomationEvent) -> None:
        with self.lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events(run_id, device_index, ts, level, type, status, message, runtime_sec, posts_processed, swipe_count, reaction_count, screenshot_count, error_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    event.index,
                    event.ts,
                    event.level,
                    event.type,
                    event.status,
                    event.message,
                    event.runtime_sec,
                    event.posts_processed,
                    event.swipe_count,
                    event.reaction_count,
                    event.screenshot_count,
                    event.error_count,
                ),
            )
