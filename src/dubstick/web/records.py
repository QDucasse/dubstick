from __future__ import annotations

import io
import logging
import math
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse

from dubstick import camelot, records
from dubstick.agreement import FULL_SOURCES, PREVIEW_SOURCES, fold_bpm
from dubstick.printer import protocol, render as label_render, service, transport
from dubstick.sources import youtube
from dubstick.settings import Settings
from dubstick.web.common import config, db, flash, render, worker

log = logging.getLogger(__name__)

router = APIRouter()


def _release(request: Request, record_id: int):
    row = db(request).one("SELECT * FROM release WHERE id = ?", (record_id,))
    if row is None:
        raise HTTPException(404, "record not found")
    return row


def _fold_marker(measured: float | None, folded: float | None) -> str | None:
    """"×2", "÷2", "×4"… when folding into the BPM range changed a reading, else None."""
    if not measured or not folded:
        return None
    # Folding only ever doubles or halves, so the ratio is a power of two.
    steps = round(math.log2(folded / measured))
    if steps == 0:
        return None
    return f"×{2 ** steps}" if steps > 0 else f"÷{2 ** -steps}"


def _tracks(request: Request, record_id: int) -> list[dict]:
    rows = db(request).query(
        "SELECT t.*, f.bpm AS final_bpm, f.camelot AS final_key, f.origin, f.agreement "
        "FROM track t LEFT JOIN track_final f ON f.track_id = t.id "
        "WHERE t.release_id = ? ORDER BY length(t.side), t.side, t.seq",
        (record_id,),
    )
    settings = Settings.load(db(request))
    opinions: dict[int, dict[str, dict]] = {}
    for op in db(request).query(
        "SELECT o.* FROM opinion o JOIN track t ON t.id = o.track_id WHERE t.release_id = ?", (record_id,)
    ):
        opinion = dict(op)
        # Show readings the way the label would use them: folded into the BPM range.
        opinion["label_bpm"] = fold_bpm(op["bpm"], settings.bpm_min, settings.bpm_max)
        opinion["fold"] = _fold_marker(op["bpm"], opinion["label_bpm"])
        opinions.setdefault(op["track_id"], {})[op["source"]] = opinion

    tracks = []
    for row in rows:
        by_source = opinions.get(row["id"], {})
        tracks.append(
            {
                **dict(row),
                # The audio the label value comes from, when the track has any.
                "audio": next((s for s in FULL_SOURCES + PREVIEW_SOURCES if s in by_source), None),
                "file_tag": by_source.get("file_tag"),
                "opinions": by_source,
            }
        )
    return tracks


def _page_count(request: Request, record_id: int) -> int:
    return len(_label_pages(request, record_id))


def _label_pages(request: Request, record_id: int):
    settings = Settings.load(db(request))
    width, length = protocol.label_dots(settings.roll_width_mm, settings.roll_length_mm)
    rows = records.label_rows(db(request), record_id)
    return label_render.render_labels(rows, width_dots=width, length_dots=length, min_cap_mm=settings.min_cap_mm)


@router.get("/records/{record_id}")
async def record_page(request: Request, record_id: int):
    return render(
        request,
        "record.html",
        release=_release(request, record_id),
        tracks=_tracks(request, record_id),
        pages=_page_count(request, record_id),
        settings=Settings.load(db(request)),
    )


@router.get("/records/{record_id}/body")
async def record_body(request: Request, record_id: int):
    # Re-rendered on live updates while the worker fills things in.
    return render(
        request,
        "partials/record_body.html",
        release=_release(request, record_id),
        tracks=_tracks(request, record_id),
        pages=_page_count(request, record_id),
        settings=Settings.load(db(request)),
    )


@router.get("/records/{record_id}/label/{page}.png")
async def label_preview(request: Request, record_id: int, page: int):
    pages = _label_pages(request, record_id)
    if not 0 <= page < len(pages):
        raise HTTPException(404)

    buffer = io.BytesIO()
    pages[page].convert("L").save(buffer, format="PNG")
    return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


def _parse_bpm(text: str) -> float | None:
    text = text.strip().replace(",", ".")
    if not text or text in ("-", "–"):
        return None
    value = float(text)
    if not 20 <= value <= 400:
        raise ValueError("BPM out of range")
    return value


def _after_edit(request: Request, record_id: int) -> Response:
    status = records.review_status(db(request), record_id)
    current = _release(request, record_id)["status"]
    if current in ("ready", "needs_review"):
        records.set_status(db(request), record_id, status)
    request.app.state.bus.publish("release", id=record_id)
    return Response(status_code=204, headers={"HX-Trigger": "record-changed"})


def _record_of(request: Request, track_id: int) -> int:
    row = db(request).one("SELECT release_id FROM track WHERE id = ?", (track_id,))
    if row is None:
        raise HTTPException(404, "track not found")
    return row["release_id"]


@router.post("/tracks/{track_id}")
async def edit_track(request: Request, track_id: int, bpm: str = Form(""), key: str = Form("")):
    record_id = _record_of(request, track_id)
    try:
        value = _parse_bpm(bpm)
    except ValueError:
        return Response("BPM must be a number between 20 and 400", status_code=422)

    code = camelot.parse(key)
    if key.strip() and key.strip() not in ("-", "–") and code is None:
        return Response("Key must be Camelot (8A) or a key name (Am, F#)", status_code=422)

    with records.watching_label(db(request), record_id):
        records.set_final(db(request), track_id, value, code)
    return _after_edit(request, record_id)


@router.post("/tracks/{track_id}/pick/{source}")
async def pick_source(request: Request, track_id: int, source: str):
    record_id = _record_of(request, track_id)
    opinion = db(request).one("SELECT bpm, camelot FROM opinion WHERE track_id = ? AND source = ?", (track_id, source))
    if opinion is None:
        raise HTTPException(404, "no such opinion")

    settings = Settings.load(db(request))
    bpm = fold_bpm(opinion["bpm"], settings.bpm_min, settings.bpm_max)
    with records.watching_label(db(request), record_id):
        records.set_final(db(request), track_id, bpm, opinion["camelot"])
    return _after_edit(request, record_id)


@router.post("/tracks/{track_id}/reset")
async def reset_track(request: Request, track_id: int):
    record_id = _record_of(request, track_id)
    settings = Settings.load(db(request))
    with records.watching_label(db(request), record_id):
        records.reset_final(db(request), track_id)
        records.refresh_finals(db(request), record_id, settings.bpm_min, settings.bpm_max)
    return _after_edit(request, record_id)


@router.post("/tracks/{track_id}/youtube")
async def youtube_track(request: Request, track_id: int):
    try:
        message = await worker(request).analyse_youtube(track_id)
    except (LookupError, RuntimeError, youtube.YouTubeError) as exc:
        return flash(request, str(exc), error=True)
    except Exception as exc:
        log.exception("YouTube analysis failed")
        return flash(request, f"YouTube download failed: {exc}", error=True)
    return flash(request, message, trigger="record-changed")


# A minute of compressed audio is a few hundred KB; this leaves room for uploaded files.
MAX_UPLOAD_BYTES = 60 * 1024 * 1024


@router.post("/tracks/{track_id}/recording")
async def recording_track(request: Request, track_id: int, audio: UploadFile = File(...)):
    """Audio recorded in the browser while the record plays, or an uploaded audio file."""
    _record_of(request, track_id)
    suffix = Path(audio.filename or "").suffix.lower()[:8] or ".audio"
    path = config(request).cache_dir / f"recording-{track_id}-{uuid.uuid4().hex[:8]}{suffix}"

    size = 0
    with path.open("wb") as out:
        while chunk := await audio.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                path.unlink(missing_ok=True)
                return flash(request, "That file is too large (60 MB at most).", error=True)
            out.write(chunk)

    try:
        message = await worker(request).analyse_recording(track_id, path)
    except (LookupError, RuntimeError) as exc:
        return flash(request, str(exc), error=True)
    return flash(request, message, trigger="record-changed")


@router.post("/records/{record_id}/reanalyze")
async def reanalyze(request: Request, record_id: int):
    release = _release(request, record_id)
    records.queue_release(db(request), release["discogs_release_id"])
    worker(request).wake()
    request.app.state.bus.publish("release", id=record_id)
    return Response(status_code=204, headers={"HX-Trigger": "record-changed"})


@router.post("/records/{record_id}/delete")
async def delete(request: Request, record_id: int):
    db(request).execute("DELETE FROM release WHERE id = ?", (record_id,))
    return RedirectResponse("/library", status_code=303)


@router.post("/records/{record_id}/print")
async def print_label(request: Request, record_id: int):
    if _release(request, record_id)["status"] != "ready":
        return flash(request, "Review the flagged tracks before printing.", error=True)

    settings = Settings.load(db(request))
    if not settings.printer_address:
        return flash(request, "No printer selected — pick one in Settings.", error=True)

    try:
        count = await service.print_rows(
            settings.printer_address,
            records.label_rows(db(request), record_id),
            density=settings.density,
            gap_mm=settings.gap_mm,
            min_cap_mm=settings.min_cap_mm,
        )
    except (transport.PrinterUnreachable, service.PrinterNotReady) as exc:
        return flash(request, str(exc), error=True)
    except Exception as exc:
        log.exception("print failed")
        return flash(request, f"Printing failed: {exc}", error=True)

    records.mark_printed(db(request), record_id)
    request.app.state.bus.publish("release", id=record_id)
    plural = "s" if count > 1 else ""
    return flash(request, f"Printed {count} label{plural}.", trigger="record-changed")
