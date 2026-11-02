"""Publisher-owned AI reservations and pause state.

This database contains no receiver observations. Migration is an explicit,
offline operation; normal writers cannot open a receiver journal by mistake.
"""

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path, *, create=False):
    """Open an existing spending store; only explicit initialization may create one."""
    path = Path(path).resolve()
    if create:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = "rwc" if create else "rw"
    database = sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True, timeout=15, factory=Connection)
    try:
        database.row_factory = sqlite3.Row
        tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables & {"aircraft", "events", "track_samples", "performance_samples"}:
            raise ValueError("The AI store must be a publisher database, not a receiver journal")
        if not create and not {"meta", "ai_runs"} <= tables:
            raise ValueError("The publisher budget store must be initialized or migrated before use")
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("PRAGMA temp_store=MEMORY")
        if create:
            database.executescript("""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ai_runs (
            id INTEGER PRIMARY KEY, started_at REAL, completed_at REAL,
            status TEXT, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            estimated_cost REAL DEFAULT 0, reserved_cost REAL DEFAULT 0,
            event_count INTEGER DEFAULT 0, purpose TEXT);
        """)
        os.chmod(path, 0o600)
    except BaseException:
        database.close()
        raise
    return database


def meta(database, key, default=None):
    row = database.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def initialize(path):
    with connect(path, create=True):
        pass


def put_meta(database, key, value):
    database.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value, allow_nan=False)))


def migrate(source, destination):
    """Copy a consistent reservation snapshot after the old paid writers stop.

    All reservations retain their IDs and conservative amounts. A paused target
    requires an explicit resume after the operator reconciles outstanding work.
    The source is read only and SQLite's backup API captures its WAL safely.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    try:
        same_file = source.samefile(destination)
    except FileNotFoundError:
        same_file = False
    if source == destination or same_file:
        raise ValueError("Source journal and publisher budget database must differ")
    with tempfile.TemporaryDirectory(prefix="wtf-publisher-budget-") as directory:
        snapshot = Path(directory) / "snapshot.sqlite3"
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(snapshot)) as backup:
                original.backup(backup)
        with closing(sqlite3.connect(snapshot)) as old, connect(destination, create=True) as new:
            new.execute("BEGIN IMMEDIATE")
            if (
                new.execute("SELECT COUNT(*) FROM ai_runs").fetchone()[0]
                or meta(new, "budget_migration")
                or meta(new, "ai_one_time_spent", 0) != 0
            ):
                raise ValueError("Publisher spending already exists; automatic merge is unsafe")
            previous_pause = meta(new, "ai_paused", False)
            # A failed copy must never leave a fresh, spendable zero-history store.
            put_meta(new, "ai_paused", True)
            new.commit()
            new.execute("BEGIN IMMEDIATE")
            columns = [row[1] for row in old.execute("PRAGMA table_info(ai_runs)")]
            allowed = [row[1] for row in new.execute("PRAGMA table_info(ai_runs)")]
            if not {"id", "started_at", "status", "estimated_cost", "reserved_cost"} <= set(columns):
                raise ValueError("Source journal has an unsupported AI reservation schema")
            kept = [column for column in allowed if column in columns]
            names = ",".join(kept)
            rows = old.execute(f"SELECT {names} FROM ai_runs ORDER BY id").fetchall()
            new.executemany(f"INSERT INTO ai_runs ({names}) VALUES ({','.join('?' for _ in kept)})", rows)
            for key in ("ai_one_time_spent", "ai_paused", "notes_rejected", "story_rejected"):
                row = old.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                if row:
                    value = json.loads(row[0])
                    if key == "ai_paused":
                        previous_pause = value
                    else:
                        put_meta(new, key, value)
            put_meta(new, "ai_paused", True)
            put_meta(new, "budget_migration", {"runs": len(rows), "previous_pause": previous_pause})
            return {"runs": len(rows), "paused": True}
