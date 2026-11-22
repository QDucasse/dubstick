from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter()


@router.get("/events")
async def events(request: Request):
    bus = request.app.state.bus

    async def stream():
        with bus.subscribe() as queue:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except TimeoutError:
                    # Keeps proxies from closing an idle connection.
                    yield {"comment": "keepalive"}
                    continue
                if await request.is_disconnected():
                    break

                # Named events let pages listen only to what concerns them.
                if event["kind"] == "release":
                    yield {"event": f"release-{event['id']}", "data": json.dumps(event)}
                yield {"event": event["kind"], "data": json.dumps(event)}

    return EventSourceResponse(stream())
