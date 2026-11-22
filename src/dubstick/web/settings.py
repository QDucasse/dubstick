from __future__ import annotations

from bleak.exc import BleakError
from fastapi import APIRouter, Form, Request

from dubstick.printer import service, transport
from dubstick.settings import Settings
from dubstick.web.common import config, db, flash, render, worker

router = APIRouter()

EDITABLE = ("printer_address", "density", "gap_mm", "min_cap_mm", "bpm_min", "bpm_max")


def _library_stats(request: Request):
    return db(request).one(
        "SELECT COUNT(*) AS files, COUNT(mb_releasegroup_id) AS tagged, COUNT(DISTINCT album) AS albums FROM local_track"
    )


def _context(request: Request, **extra):
    cfg = config(request)
    return {
        "settings": Settings.load(db(request)),
        "has_discogs": bool(cfg.discogs_token),
        "music_roots": cfg.music_roots,
        "library": _library_stats(request),
        "indexing": worker(request).indexing,
        "report": request.app.state.printer_report,
        **extra,
    }


@router.get("/settings")
async def settings_page(request: Request):
    return render(request, "settings.html", **_context(request))


@router.post("/settings")
async def save_settings(request: Request):
    form = await request.form()
    settings = Settings.load(db(request))
    errors = settings.update(**{name: str(form[name]) for name in EDITABLE if name in form})
    if not errors:
        settings.save(db(request))
    message = "; ".join(errors) if errors else "Settings saved."
    return flash(request, message, error=bool(errors))


@router.post("/settings/printer/scan")
async def scan_printers(request: Request):
    try:
        printers = await transport.scan()
        error = "" if printers else "No P21 found. Is it switched on and not connected to the phone app?"
    except (BleakError, OSError) as exc:
        printers, error = [], f"Bluetooth scan failed: {exc}"
    return render(request, "partials/printers.html", printers=printers, error=error)


@router.post("/settings/printer/select")
async def select_printer(request: Request, address: str = Form(...)):
    settings = Settings.load(db(request))
    settings.printer_address = address
    settings.save(db(request))
    return flash(request, f"Printer set to {address}.", trigger="settings-changed")


@router.get("/printer/status")
async def printer_status(request: Request):
    settings = Settings.load(db(request))
    if not settings.printer_address:
        return render(request, "partials/printer_status.html", report=None, configured=False)

    report = await service.status(settings.printer_address)
    request.app.state.printer_report = report

    if report.label:
        # Remember the roll so previews match what will be printed.
        settings.roll_width_mm = report.label.width_mm
        settings.roll_length_mm = report.label.length_mm
        settings.save(db(request))
    return render(request, "partials/printer_status.html", report=report, configured=True)


@router.post("/settings/reindex")
async def reindex(request: Request):
    if not config(request).music_roots:
        return flash(request, "No music folders configured (DUBSTICK_MUSIC).", error=True)

    updated, removed = await worker(request).reindex_library()
    message = f"Library re-indexed: {updated} files updated, {removed} removed."
    return flash(request, message, trigger="settings-changed")
