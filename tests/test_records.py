import pytest

from dubstick import records
from dubstick.db import Database
from dubstick.sources.discogs import Release, Track


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.init()
    return database


def make_release(*positions):
    tracks = [Track(p, p[0], int(p[1:]), f"Track {p}", "Artist", 300) for p in positions]
    return Release(1, None, "Artist", "Title", "Label", "CAT001", 2000, "DE", "Vinyl", "", tracks)


def test_queue_is_idempotent(db):
    first = records.queue_release(db, 42)
    records.set_status(db, first, "ready")
    assert records.queue_release(db, 42) == first
    assert db.one("SELECT status FROM release WHERE id = ?", (first,))["status"] == "queued"


def test_label_rows_follow_sides(db):
    record = records.queue_release(db, 1)
    records.save_release(db, record, make_release("A1", "A2", "B1"))
    tracks = {r["position"]: r["id"] for r in db.query("SELECT id, position FROM track")}
    records.save_opinion(db, tracks["A1"], "essentia_deezer", bpm=70, camelot="8A")
    records.save_opinion(db, tracks["A1"], "essentia_local", bpm=140, camelot="8A")
    records.save_opinion(db, tracks["B1"], "essentia_youtube", bpm=128, camelot="5B", bpm_confidence=3.0, key_strength=0.7)

    assert records.refresh_finals(db, record, 90, 180) == "needs_review"  # A2 has nothing

    rows = records.label_rows(db, record)
    assert [r.side for r in rows] == ["A", "B"]
    assert [(e.bpm, e.camelot) for e in rows[0].entries] == [(140, "8A"), (None, None)]


def test_user_edit_survives_reanalysis(db):
    record = records.queue_release(db, 1)
    records.save_release(db, record, make_release("A1"))
    track = db.one("SELECT id FROM track")["id"]
    records.save_opinion(db, track, "essentia_deezer", bpm=120, camelot="1A")
    records.refresh_finals(db, record, 90, 180)

    records.set_final(db, track, 125, "2A")
    records.save_opinion(db, track, "essentia_youtube", bpm=130, camelot="9B")
    records.refresh_finals(db, record, 90, 180)

    final = db.one("SELECT * FROM track_final WHERE track_id = ?", (track,))
    assert (final["bpm"], final["camelot"], final["origin"], final["agreement"]) == (125, "2A", "user", "disagree")
    assert records.review_status(db, record) == "ready"


def test_tracklist_update_keeps_existing_tracks(db):
    record = records.queue_release(db, 1)
    records.save_release(db, record, make_release("A1", "B1"))
    a1 = db.one("SELECT id FROM track WHERE position = 'A1'")["id"]
    records.save_release(db, record, make_release("A1", "B2"))

    positions = {r["position"]: r["id"] for r in db.query("SELECT id, position FROM track")}
    assert positions.keys() == {"A1", "B2"}
    assert positions["A1"] == a1


def test_old_databases_gain_new_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE opinion (id INTEGER PRIMARY KEY, track_id INTEGER, source TEXT, bpm REAL, "
                     "camelot TEXT, confidence REAL, raw_json TEXT, created_at TEXT, UNIQUE (track_id, source))")
        conn.execute("CREATE TABLE track (id INTEGER PRIMARY KEY, release_id INTEGER, position TEXT, side TEXT, "
                     "seq INTEGER, title TEXT, artists TEXT, duration_s INTEGER, local_path TEXT, audio_source TEXT)")
    Database(path).init()
    with sqlite3.connect(path) as conn:
        opinion = {row[1] for row in conn.execute("PRAGMA table_info(opinion)")}
        track = {row[1] for row in conn.execute("PRAGMA table_info(track)")}
    assert {"bpm_confidence", "key_strength"} <= opinion
    assert "note" in track
