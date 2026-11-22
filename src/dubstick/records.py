from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from dubstick import agreement
from dubstick.db import Database
from dubstick.printer.render import Entry, Row
from dubstick.sources.discogs import Release

STATUSES = ("queued", "analyzing", "ready", "needs_review", "failed")


def queue_release(db: Database, release_id: int, *, title: str = "", thumb: str = "") -> int:
    """Add a Discogs release to the library, or re-queue it if it's already there."""
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM release WHERE discogs_release_id = ?", (release_id,)).fetchone()
        if row:
            # A new analysis may change the values, so the printed label is outdated.
            conn.execute(
                "UPDATE release SET status = 'queued', error = NULL, printed_at = NULL, "
                "updated_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            return row["id"]
        return conn.execute(
            "INSERT INTO release (discogs_release_id, title, thumb_url) VALUES (?, ?, ?)",
            (release_id, title, thumb),
        ).lastrowid


def save_release(db: Database, record_id: int, release: Release) -> None:
    """Store Discogs metadata and tracklist, keeping user edits of tracks that still exist."""
    with db.connect() as conn:
        conn.execute(
            "UPDATE release SET discogs_master_id = ?, artist = ?, title = ?, label = ?, catno = ?, "
            "year = ?, country = ?, format = ?, thumb_url = ?, updated_at = datetime('now') WHERE id = ?",
            (release.master_id, release.artist, release.title, release.label, release.catno,
             release.year, release.country, release.format, release.thumb, record_id),
        )

        existing = {
            row["position"]: row["id"]
            for row in conn.execute("SELECT id, position FROM track WHERE release_id = ?", (record_id,))
        }
        wanted = {track.position for track in release.tracks}
        for position, track_id in existing.items():
            if position not in wanted:
                conn.execute("DELETE FROM track WHERE id = ?", (track_id,))

        for track in release.tracks:
            values = (track.side, track.seq, track.title, track.artists, track.duration_s)
            if track.position in existing:
                conn.execute(
                    "UPDATE track SET side = ?, seq = ?, title = ?, artists = ?, duration_s = ? WHERE id = ?",
                    (*values, existing[track.position]),
                )
            else:
                conn.execute(
                    "INSERT INTO track (release_id, position, side, seq, title, artists, duration_s) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (record_id, track.position, *values),
                )


def set_status(db: Database, record_id: int, status: str, *, progress: str = "", error: str | None = None) -> None:
    db.execute(
        "UPDATE release SET status = ?, progress = ?, error = ?, updated_at = datetime('now') WHERE id = ?",
        (status, progress, error, record_id),
    )


def mark_printed(db: Database, record_id: int) -> None:
    db.execute("UPDATE release SET printed_at = datetime('now') WHERE id = ?", (record_id,))


@contextmanager
def watching_label(db: Database, record_id: int) -> Iterator[None]:
    """Send a printed record back to the queue if the change made inside alters its label."""
    before = label_rows(db, record_id)
    yield
    if label_rows(db, record_id) != before:
        db.execute("UPDATE release SET printed_at = NULL WHERE id = ?", (record_id,))


def set_progress(db: Database, record_id: int, progress: str) -> None:
    db.execute("UPDATE release SET progress = ? WHERE id = ?", (progress, record_id))


def save_opinion(
    db: Database,
    track_id: int,
    source: str,
    *,
    bpm: float | None,
    camelot: str | None,
    bpm_confidence: float | None = None,
    key_strength: float | None = None,
    raw: dict | None = None,
) -> None:
    db.execute(
        "INSERT INTO opinion (track_id, source, bpm, camelot, bpm_confidence, key_strength, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(track_id, source) DO UPDATE SET bpm = excluded.bpm, camelot = excluded.camelot, "
        "bpm_confidence = excluded.bpm_confidence, key_strength = excluded.key_strength, "
        "raw_json = excluded.raw_json, created_at = datetime('now')",
        (track_id, source, bpm, camelot, bpm_confidence, key_strength, json.dumps(raw) if raw else None),
    )


def opinions_for(conn: sqlite3.Connection, track_id: int) -> list[agreement.Opinion]:
    rows = conn.execute(
        "SELECT source, bpm, camelot, bpm_confidence, key_strength FROM opinion WHERE track_id = ?", (track_id,)
    )
    return [
        agreement.Opinion(r["source"], r["bpm"], r["camelot"], r["bpm_confidence"], r["key_strength"])
        for r in rows
    ]


def refresh_finals(db: Database, record_id: int, bpm_min: float, bpm_max: float) -> str:
    """Recompute automatic values for every track; returns the record's resulting status."""
    needs_review = False
    with db.connect() as conn:
        tracks = conn.execute("SELECT id FROM track WHERE release_id = ?", (record_id,)).fetchall()
        for track in tracks:
            verdict = agreement.judge(opinions_for(conn, track["id"]), bpm_min, bpm_max)
            current = conn.execute("SELECT origin FROM track_final WHERE track_id = ?", (track["id"],)).fetchone()

            if current and current["origin"] == "user":
                # A user edit is final; only the agreement badge follows the sources.
                conn.execute("UPDATE track_final SET agreement = ? WHERE track_id = ?", (verdict.agreement, track["id"]))
                continue

            conn.execute(
                "INSERT INTO track_final (track_id, bpm, camelot, origin, agreement) VALUES (?, ?, ?, 'auto', ?) "
                "ON CONFLICT(track_id) DO UPDATE SET bpm = excluded.bpm, camelot = excluded.camelot, "
                "origin = 'auto', agreement = excluded.agreement",
                (track["id"], verdict.bpm, verdict.camelot, verdict.agreement),
            )
            if verdict.agreement in agreement.NEEDS_REVIEW:
                needs_review = True

    return "needs_review" if needs_review else "ready"


def set_final(db: Database, track_id: int, bpm: float | None, camelot: str | None) -> None:
    db.execute(
        "INSERT INTO track_final (track_id, bpm, camelot, origin) VALUES (?, ?, ?, 'user') "
        "ON CONFLICT(track_id) DO UPDATE SET bpm = excluded.bpm, camelot = excluded.camelot, origin = 'user'",
        (track_id, bpm, camelot),
    )


def reset_final(db: Database, track_id: int) -> None:
    db.execute("UPDATE track_final SET origin = 'auto' WHERE track_id = ?", (track_id,))


def review_status(db: Database, record_id: int) -> str:
    """A record is reviewed once every unclear track has been settled by hand."""
    row = db.one(
        "SELECT COUNT(*) AS open FROM track t LEFT JOIN track_final f ON f.track_id = t.id "
        "WHERE t.release_id = ? AND COALESCE(f.origin, 'auto') = 'auto' "
        f"AND COALESCE(f.agreement, 'none') IN ({','.join('?' * len(agreement.NEEDS_REVIEW))})",
        (record_id, *agreement.NEEDS_REVIEW),
    )
    return "needs_review" if row["open"] else "ready"


def label_rows(db: Database, record_id: int) -> list[Row]:
    tracks = db.query(
        "SELECT t.side, f.bpm, f.camelot FROM track t LEFT JOIN track_final f ON f.track_id = t.id "
        "WHERE t.release_id = ? ORDER BY length(t.side), t.side, t.seq",
        (record_id,),
    )
    rows: dict[str, list[Entry]] = {}
    for track in tracks:
        rows.setdefault(track["side"], []).append(Entry(track["bpm"], track["camelot"]))
    return [Row(side, tuple(entries)) for side, entries in rows.items()]
