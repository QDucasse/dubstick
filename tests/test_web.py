import pytest
from fastapi.testclient import TestClient

from dubstick import records
from dubstick.app import create_app
from dubstick.config import Config
from dubstick.sources.discogs import Release, Track


@pytest.fixture
def client(tmp_path):
    config = Config(data_dir=tmp_path, discogs_token=None, essentia_workers=1)
    with TestClient(create_app(config)) as test_client:
        yield test_client


@pytest.fixture
def record(client):
    db = client.app.state.db
    record_id = records.queue_release(db, 7)
    release = Release(7, None, "Artist", "Title", "Label", "CAT", 1999, "US", "Vinyl", "",
                      [Track("A1", "A", 1, "One", "Artist", 300), Track("B1", "B", 1, "Two", "Artist", 300)])
    records.save_release(db, record_id, release)
    records.set_status(db, record_id, "needs_review")
    return record_id


@pytest.mark.parametrize("path", ["/", "/library", "/settings", "/queue", "/library?bpm_min=120&key=8A&compatible=true"])
def test_pages_render(client, path):
    assert client.get(path).status_code == 200


def test_record_page_and_preview(client, record):
    page = client.get(f"/records/{record}")
    assert page.status_code == 200 and "One" in page.text
    preview = client.get(f"/records/{record}/label/0.png")
    assert preview.headers["content-type"] == "image/png"


def test_edit_track_validates_and_settles_review(client, record):
    tracks = client.app.state.db.query("SELECT id FROM track ORDER BY id")
    assert client.post(f"/tracks/{tracks[0]['id']}", data={"bpm": "fast", "key": ""}).status_code == 422
    assert client.post(f"/tracks/{tracks[0]['id']}", data={"bpm": "128", "key": "H"}).status_code == 422

    for track in tracks:
        assert client.post(f"/tracks/{track['id']}", data={"bpm": "128", "key": "Am"}).status_code == 204

    db = client.app.state.db
    assert db.one("SELECT status FROM release WHERE id = ?", (record,))["status"] == "ready"
    assert db.one("SELECT camelot FROM track_final LIMIT 1")["camelot"] == "8A"

    found = client.get("/library?key=9A&compatible=true", headers={"HX-Request": "true"})
    assert "2 tracks" in found.text


def test_print_refused_until_reviewed(client, record):
    response = client.post(f"/records/{record}/print")
    assert "Review the flagged tracks" in response.text


def test_print_without_printer(client, record):
    records.set_status(client.app.state.db, record, "ready")
    response = client.post(f"/records/{record}/print")
    assert "No printer selected" in response.text


def test_queue_lists_unprinted_records_only(client, record):
    db = client.app.state.db
    records.set_status(db, record, "ready")
    assert "Title" in client.get("/queue").text

    records.mark_printed(db, record)
    assert "Title" not in client.get("/queue").text

    track = db.one("SELECT id FROM track LIMIT 1")["id"]
    client.post(f"/tracks/{track}", data={"bpm": "133", "key": "4A"})
    assert "Title" in client.get("/queue").text, "an edited label must be printed again"


def test_recording_is_analysed_then_discarded(client, record, monkeypatch):
    seen = {}

    async def fake_analyse(track_id, path):
        seen["exists"] = path.exists()
        seen["data"] = path.read_bytes()
        seen["suffix"] = path.suffix
        path.unlink()
        return "Recording analysed."

    monkeypatch.setattr(client.app.state.worker, "analyse_recording", fake_analyse)
    track = client.app.state.db.one("SELECT id FROM track LIMIT 1")["id"]
    response = client.post(f"/tracks/{track}/recording", files={"audio": ("recording.m4a", b"fake audio", "audio/mp4")})

    assert "Recording analysed." in response.text
    assert response.headers["HX-Trigger"] == "record-changed"
    assert seen == {"exists": True, "data": b"fake audio", "suffix": ".m4a"}
    assert not list(client.app.state.config.cache_dir.glob("recording-*"))


def test_recording_for_unknown_track(client):
    response = client.post("/tracks/999/recording", files={"audio": ("a.webm", b"x", "audio/webm")})
    assert response.status_code == 404


def test_fold_marker():
    from dubstick.web.records import _fold_marker

    assert _fold_marker(77.5, 155.0) == "×2"
    assert _fold_marker(280.0, 140.0) == "÷2"
    assert _fold_marker(40.0, 160.0) == "×4"
    assert _fold_marker(128.0, 128.0) is None
    assert _fold_marker(None, None) is None
