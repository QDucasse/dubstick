from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "dubstick/0.1"


class RateLimiter:
    """Space out calls so a service sees at most one request per `interval` seconds."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            delay = self._next - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = max(loop.time(), self._next) + self.interval


def client(**kwargs) -> httpx.AsyncClient:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    return httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True, **kwargs)


async def get_json(
    http: httpx.AsyncClient,
    limiter: RateLimiter,
    url: str,
    params: dict | None = None,
    retries: int = 3,
):
    for attempt in range(retries):
        await limiter.wait()
        response = await http.get(url, params=params)

        if response.status_code == 429 or response.status_code >= 500:
            # Back off, honouring Retry-After when the service sends one.
            delay = float(response.headers.get("Retry-After", 2 ** (attempt + 1)))
            log.info("%s answered %s, retrying in %.0fs", url, response.status_code, delay)
            await asyncio.sleep(delay)
            continue

        response.raise_for_status()
        return response.json()

    response.raise_for_status()
    return response.json()
