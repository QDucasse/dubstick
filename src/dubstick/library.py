from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import mutagen
from rapidfuzz import fuzz

from dubstick.db import Database

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff", ".aif"}


def _first(tags, *names: str) -> str:
    for name in names:
        value = tags.get(name) if tags else None
        if value:
            text = value[0] if isinstance(value, list) else value
            return str(text).strip()
    return ""


def _float(text: str) -> float | None:
    try:
        return float(text) or None
    except ValueError:
        return None


def read_tags(path: Path) -> dict | None:
    try:
        # "easy" mode maps ID3, MP4 and Vorbis frames onto the same key names.
        audio = mutagen.File(path, easy=True)
    except Exception as exc:  # mutagen raises many unrelated types on broken files
        log.debug("unreadable %s: %s", path, exc)
        return None
    if audio is None:
        return None

    tags = audio.tags or {}
    return {
        "mb_track_id": _first(tags, "musicbrainz_trackid") or None,
        "mb_release_id": _first(tags, "musicbrainz_albumid") or None,
        "mb_releasegroup_id": _first(tags, "musicbrainz_releasegroupid") or None,
        "artist": _first(tags, "artist"),
        "albumartist": _first(tags, "albumartist", "artist"),
        "album": _first(tags, "album"),
        "title": _first(tags, "title") or path.stem,
        "duration_s": getattr(audio.info, "length", None),
        "bpm_tag": _float(_first(tags, "bpm")),
    }


BATCH_SIZE = 200

UPSERT = (
    "INSERT OR REPLACE INTO local_track (path, mb_track_id, mb_release_id, mb_releasegroup_id, "
    "artist, albumartist, album, title, duration_s, bpm_tag, mtime) "
    "VALUES (:path, :mb_track_id, :mb_release_id, :mb_releasegroup_id, :artist, "
    ":albumartist, :album, :title, :duration_s, :bpm_tag, :mtime)"
)


def _audio_files(roots: list[Path]):
    for root in roots:
        for directory, _dirs, files in os.walk(root):
            for name in files:
                path = Path(directory) / name
                if path.suffix.lower() in AUDIO_EXTENSIONS:
                    yield path


def reindex(db: Database, roots: list[Path]) -> tuple[int, int]:
    """Bring the index in line with the files on disk. Returns (updated, removed)."""
    known = {row["path"]: row["mtime"] for row in db.query("SELECT path, mtime FROM local_track")}
    seen: set[str] = set()
    batch: list[dict] = []
    updated = 0

    def flush() -> None:
        # Short transactions, so the analysis worker can write in between.
        with db.connect() as conn:
            conn.executemany(UPSERT, batch)
        batch.clear()

    for path in _audio_files(roots):
        key = str(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            # Moved or deleted while we walked: it'll be picked up, or forgotten, next time.
            continue
        seen.add(key)
        if known.get(key) == mtime:
            continue

        tags = read_tags(path)
        if tags is None:
            continue
        batch.append({**tags, "path": key, "mtime": mtime})
        updated += 1
        if len(batch) >= BATCH_SIZE:
            flush()
    flush()

    # Only forget files under roots we actually walked, so an unmounted disk doesn't wipe its entries.
    walked = [str(root).rstrip(os.sep) + os.sep for root in roots if root.is_dir()]
    gone = [p for p in known if p not in seen and any(p.startswith(r) for r in walked)]
    with db.connect() as conn:
        conn.executemany("DELETE FROM local_track WHERE path = ?", [(p,) for p in gone])

    return updated, len(gone)


@dataclass(frozen=True)
class LocalTrack:
    path: str
    title: str
    duration_s: float | None
    bpm_tag: float | None


def find_album(
    db: Database,
    *,
    releasegroup_ids: set[str],
    release_ids: set[str],
    artist: str,
    album: str,
) -> list[LocalTrack]:
    """Local files of the album matching a Discogs release, by MusicBrainz IDs then by name."""
    rows = []
    if release_ids:
        marks = ",".join("?" * len(release_ids))
        rows = db.query(f"SELECT * FROM local_track WHERE mb_release_id IN ({marks})", tuple(release_ids))
    if not rows and releasegroup_ids:
        marks = ",".join("?" * len(releasegroup_ids))
        rows = db.query(f"SELECT * FROM local_track WHERE mb_releasegroup_id IN ({marks})", tuple(releasegroup_ids))
    if not rows:
        rows = _fuzzy_album(db, artist, album)

    return [LocalTrack(r["path"], r["title"], r["duration_s"], r["bpm_tag"]) for r in rows]


def _fuzzy_album(db: Database, artist: str, album: str) -> list:
    albums = db.query("SELECT DISTINCT albumartist, album FROM local_track WHERE album != ''")
    best, best_score = None, 0.0
    for row in albums:
        album_score = fuzz.token_set_ratio(album.lower(), row["album"].lower())
        artist_score = fuzz.token_set_ratio(artist.lower(), row["albumartist"].lower())
        if album_score >= 90 and artist_score >= 80 and album_score + artist_score > best_score:
            best, best_score = row, album_score + artist_score
    if best is None:
        return []
    return db.query(
        "SELECT * FROM local_track WHERE albumartist = ? AND album = ?",
        (best["albumartist"], best["album"]),
    )


def match_track(title: str, duration_s: int | None, candidates: list[LocalTrack]) -> LocalTrack | None:
    """Pick the file for a vinyl track by title and length; side order often differs from digital."""
    best, best_score = None, 0.0
    for candidate in candidates:
        score = fuzz.token_set_ratio(title.lower(), candidate.title.lower())
        if duration_s and candidate.duration_s:
            gap = abs(candidate.duration_s - duration_s)
            # Vinyl and digital timings drift a few seconds; a minute apart is another version.
            if gap > 45:
                continue
            score -= gap / 3
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= 75 else None
