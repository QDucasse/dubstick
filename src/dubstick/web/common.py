from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from dubstick.config import Config
from dubstick.db import Database
from dubstick.worker import Worker

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

STATUS_LABELS = {
    "queued": "queued",
    "analyzing": "analyzing",
    "ready": "ready",
    "needs_review": "needs review",
    "failed": "failed",
}
AGREEMENT_LABELS = {
    "agree": "sources agree",
    "disagree": "sources disagree",
    "single": "single source",
    "weak": "low confidence",
    "none": "no data",
}
SOURCE_LABELS = {
    "essentia_local": "local file",
    "essentia_recording": "your recording",
    "essentia_youtube": "YouTube",
    "essentia_deezer": "Deezer preview",
    "file_tag": "file tag",
}
templates.env.globals.update(
    status_labels=STATUS_LABELS, agreement_labels=AGREEMENT_LABELS, source_labels=SOURCE_LABELS
)


def _bpm(value) -> str:
    return "–" if value is None else str(round(value))


templates.env.filters["bpm"] = _bpm


def db(request: Request) -> Database:
    return request.app.state.db


def worker(request: Request) -> Worker:
    return request.app.state.worker


def config(request: Request) -> Config:
    return request.app.state.config


def render(request: Request, name: str, status_code: int = 200, **context):
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def flash(request: Request, message: str, *, error: bool = False, trigger: str | None = None):
    response = render(request, "partials/flash.html", message=message, error=error)
    if trigger:
        # Lets other parts of the page refresh themselves after this action.
        response.headers["HX-Trigger"] = trigger
    return response
