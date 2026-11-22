from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from dubstick.config import Config
from dubstick.db import Database
from dubstick.events import EventBus
from dubstick.web import library, records, scan, settings, stream
from dubstick.worker import Worker

PACKAGE_DIR = Path(__file__).parent


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config.prepare()
        db = Database(config.db_path)
        db.init()
        bus = EventBus()
        worker = Worker(config, db, bus)

        app.state.config = config
        app.state.db = db
        app.state.bus = bus
        app.state.worker = worker
        app.state.printer_report = None

        await worker.start()
        try:
            yield
        finally:
            await worker.stop()

    app = FastAPI(title="dubstick", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    for module in (scan, records, library, settings, stream):
        app.include_router(module.router)
    return app


def run() -> None:
    logging.basicConfig(
        level=os.environ.get("DUBSTICK_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        "dubstick.app:create_app",
        factory=True,
        host=os.environ.get("DUBSTICK_HOST", "0.0.0.0"),
        port=int(os.environ.get("DUBSTICK_PORT", "8000")),
        # Behind Traefik: trust its X-Forwarded-* headers.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
