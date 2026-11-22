from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS release (
    id INTEGER PRIMARY KEY,
    discogs_release_id INTEGER NOT NULL UNIQUE,
    discogs_master_id INTEGER,
    artist TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    catno TEXT NOT NULL DEFAULT '',
    year INTEGER,
    country TEXT NOT NULL DEFAULT '',
    format TEXT NOT NULL DEFAULT '',
    thumb_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    progress TEXT NOT NULL DEFAULT '',
    local_album TEXT,
    printed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    error TEXT
);

CREATE TABLE IF NOT EXISTS track (
    id INTEGER PRIMARY KEY,
    release_id INTEGER NOT NULL REFERENCES release(id) ON DELETE CASCADE,
    position TEXT NOT NULL,
    side TEXT NOT NULL,
    seq INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    artists TEXT NOT NULL DEFAULT '',
    duration_s INTEGER,
    local_path TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS track_release ON track(release_id);

CREATE TABLE IF NOT EXISTS opinion (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL REFERENCES track(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    bpm REAL,
    camelot TEXT,
    bpm_confidence REAL,
    key_strength REAL,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (track_id, source)
);

CREATE TABLE IF NOT EXISTS track_final (
    track_id INTEGER PRIMARY KEY REFERENCES track(id) ON DELETE CASCADE,
    bpm REAL,
    camelot TEXT,
    origin TEXT NOT NULL DEFAULT 'auto',
    agreement TEXT NOT NULL DEFAULT 'none'
);
CREATE INDEX IF NOT EXISTS track_final_bpm ON track_final(bpm);
CREATE INDEX IF NOT EXISTS track_final_key ON track_final(camelot);

CREATE TABLE IF NOT EXISTS local_track (
    path TEXT PRIMARY KEY,
    mb_track_id TEXT,
    mb_release_id TEXT,
    mb_releasegroup_id TEXT,
    artist TEXT NOT NULL DEFAULT '',
    albumartist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    duration_s REAL,
    bpm_tag REAL,
    mtime REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS local_track_rg ON local_track(mb_releasegroup_id);
CREATE INDEX IF NOT EXISTS local_track_release ON local_track(mb_release_id);

CREATE TABLE IF NOT EXISTS setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# Columns added after the first release, by table.
ADDED_COLUMNS = {
    "opinion": {"bpm_confidence": "REAL", "key_strength": "REAL"},
    "track": {"note": "TEXT"},
    "release": {"printed_at": "TEXT"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


class Database:
    def __init__(self, path: Path):
        self.path = path

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            # WAL lets the web pages read while the worker writes.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            _migrate(conn)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._open()
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        with self.connect() as conn:
            return conn.execute(sql, params).lastrowid
