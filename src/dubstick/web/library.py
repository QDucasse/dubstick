from __future__ import annotations

from fastapi import APIRouter, Request

from dubstick import camelot
from dubstick.records import STATUSES
from dubstick.web.common import db, is_htmx, render

router = APIRouter()

# Records that need attention float to the top.
STATUS_ORDER = "CASE status WHEN 'needs_review' THEN 0 WHEN 'failed' THEN 1 WHEN 'analyzing' THEN 2 WHEN 'queued' THEN 3 ELSE 4 END"


def _float(text: str | None) -> float | None:
    try:
        return float(text) if text and text.strip() else None
    except ValueError:
        return None


@router.get("/library")
async def library_page(
    request: Request,
    status: str = "",
    q: str = "",
    bpm_min: str = "",
    bpm_max: str = "",
    key: str = "",
    compatible: bool = False,
):
    low, high = _float(bpm_min), _float(bpm_max)
    code = camelot.parse(key)
    track_search = low is not None or high is not None or code is not None

    context = {
        "status": status if status in STATUSES else "",
        "q": q,
        "bpm_min": bpm_min,
        "bpm_max": bpm_max,
        "key": key,
        "compatible": compatible,
        "key_error": bool(key.strip()) and code is None,
        "statuses": STATUSES,
        "track_search": track_search,
        "counts": {r["status"]: r["n"] for r in db(request).query("SELECT status, COUNT(*) AS n FROM release GROUP BY status")},
    }

    if track_search:
        keys = camelot.compatible(code) if code and compatible else ([code] if code else [])
        context["tracks"] = _search_tracks(request, low, high, keys, context["status"], q)
        context["keys"] = keys
    else:
        context["releases"] = _list_releases(request, context["status"], q)

    template = "partials/library_results.html" if is_htmx(request) else "library.html"
    return render(request, template, **context)


def _text_filter(q: str) -> tuple[str, list]:
    if not q.strip():
        return "", []
    like = f"%{q.strip()}%"
    return " AND (r.artist LIKE ? OR r.title LIKE ? OR r.catno LIKE ? OR r.label LIKE ?)", [like] * 4


def _list_releases(request: Request, status: str, q: str):
    sql = (
        "SELECT r.*, (SELECT COUNT(*) FROM track t WHERE t.release_id = r.id) AS track_count "
        "FROM release r WHERE 1 = 1"
    )
    params: list = []
    if status:
        sql += " AND r.status = ?"
        params.append(status)
    text, text_params = _text_filter(q)
    sql += text + f" ORDER BY {STATUS_ORDER.replace('status', 'r.status')}, r.updated_at DESC"
    return db(request).query(sql, tuple(params + text_params))


def _search_tracks(request: Request, low: float | None, high: float | None, keys: list[str], status: str, q: str):
    sql = (
        "SELECT t.id, t.position, t.title, t.artists, f.bpm, f.camelot, f.agreement, f.origin, "
        "r.id AS record_id, r.artist AS release_artist, r.title AS release_title, r.catno "
        "FROM track t JOIN track_final f ON f.track_id = t.id JOIN release r ON r.id = t.release_id WHERE 1 = 1"
    )
    params: list = []
    # Rounded like on the label, so "128" finds a track printed as 128.
    if low is not None:
        sql += " AND ROUND(f.bpm) >= ?"
        params.append(low)
    if high is not None:
        sql += " AND ROUND(f.bpm) <= ?"
        params.append(high)
    if keys:
        sql += f" AND f.camelot IN ({','.join('?' * len(keys))})"
        params += keys
    if status:
        sql += " AND r.status = ?"
        params.append(status)
    text, text_params = _text_filter(q)
    sql += text + " ORDER BY f.bpm, f.camelot LIMIT 500"
    return db(request).query(sql, tuple(params + text_params))
