from __future__ import annotations

import httpx
from fastapi import APIRouter, Form, Request

from dubstick import records
from dubstick.web.common import config, db, render, worker

router = APIRouter()


def _queue_rows(request: Request):
    # Every record still waiting for its label; printing one takes it off the list.
    return db(request).query(
        "SELECT id, artist, title, catno, status, progress FROM release WHERE printed_at IS NULL "
        "ORDER BY CASE status WHEN 'analyzing' THEN 0 WHEN 'queued' THEN 1 WHEN 'needs_review' THEN 2 "
        "WHEN 'failed' THEN 3 ELSE 4 END, updated_at DESC LIMIT 30"
    )


@router.get("/")
async def scan_page(request: Request):
    return render(
        request,
        "scan.html",
        queue=_queue_rows(request),
        has_token=bool(config(request).discogs_token),
    )


@router.get("/queue")
async def queue_strip(request: Request):
    return render(request, "partials/queue.html", queue=_queue_rows(request))


@router.post("/search")
async def search(
    request: Request,
    barcode: str = Form(""),
    catno: str = Form(""),
    query: str = Form(""),
):
    if not (barcode.strip() or catno.strip() or query.strip()):
        return render(request, "partials/results.html", results=[], message="Type or scan something first.")

    try:
        results = await worker(request).discogs.search(barcode=barcode, catno=catno, query=query)
    except httpx.HTTPStatusError as exc:
        hint = " Is DISCOGS_TOKEN set?" if exc.response.status_code == 401 else ""
        return render(request, "partials/results.html", results=[], message=f"Discogs answered {exc.response.status_code}.{hint}")
    except httpx.HTTPError as exc:
        return render(request, "partials/results.html", results=[], message=f"Discogs unreachable: {exc}")

    if len(results) == 1:
        # Nothing to choose from: queue it straight away.
        return await _queue(request, results[0].id, results[0].title, results[0].thumb)

    message = "" if results else "No release found on Discogs."
    return render(request, "partials/results.html", results=results, message=message)


@router.post("/queue/{release_id}")
async def queue(request: Request, release_id: int, title: str = Form(""), thumb: str = Form("")):
    return await _queue(request, release_id, title, thumb)


async def _queue(request: Request, release_id: int, title: str, thumb: str):
    record_id = records.queue_release(db(request), release_id, title=title, thumb=thumb)
    worker(request).wake()
    request.app.state.bus.publish("release", id=record_id)

    response = render(request, "partials/queued.html", record_id=record_id, title=title)
    # Clear the inputs and go back to scanning.
    response.headers["HX-Trigger"] = "queued"
    return response
