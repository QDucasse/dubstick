from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

from dubstick.sources.http import RateLimiter, client, get_json

API = "https://api.deezer.com"

# Deezer allows 50 requests per 5 seconds; stay well below.
_limiter = RateLimiter(0.2)

MIN_TITLE_SCORE = 80
MIN_ARTIST_SCORE = 70


@dataclass(frozen=True)
class Preview:
    deezer_id: int
    title: str
    artist: str
    duration_s: int
    url: str


def _score(candidate: dict, artist: str, title: str) -> float:
    title_score = fuzz.token_set_ratio(title.lower(), candidate.get("title", "").lower())
    artist_score = fuzz.token_set_ratio(artist.lower(), candidate.get("artist", {}).get("name", "").lower())
    if title_score < MIN_TITLE_SCORE or artist_score < MIN_ARTIST_SCORE:
        return 0
    return title_score + artist_score


async def find_preview(artist: str, title: str, duration_s: int | None = None) -> Preview | None:
    """Best Deezer match with a 30 s preview, or None when nothing is close enough."""
    queries = [f'artist:"{artist}" track:"{title}"', f"{artist} {title}"]
    async with client() as http:
        for query in queries:
            data = await get_json(http, _limiter, f"{API}/search", {"q": query, "limit": 15})
            candidates = [c for c in data.get("data") or [] if c.get("preview")]
            scored = [(_score(c, artist, title), c) for c in candidates]
            scored = [(s, c) for s, c in scored if s > 0]
            if not scored:
                continue

            if duration_s:
                # Prefer the version whose length matches the record (original vs. remix/edit).
                scored = [(s - min(abs(c.get("duration", 0) - duration_s), 60) / 3, c) for s, c in scored]
            best = max(scored, key=lambda pair: pair[0])[1]
            return Preview(
                deezer_id=best["id"],
                title=best.get("title", ""),
                artist=best.get("artist", {}).get("name", ""),
                duration_s=best.get("duration", 0),
                url=best["preview"],
            )
    return None


async def download(preview: Preview, cache_dir: Path) -> Path:
    path = cache_dir / f"deezer-{preview.deezer_id}.mp3"
    if path.exists() and path.stat().st_size > 0:
        return path

    async with client() as http:
        response = await http.get(preview.url)
        response.raise_for_status()
    # Write then rename, so a crash never leaves a truncated file in the cache.
    partial = path.with_suffix(".part")
    partial.write_bytes(response.content)
    partial.rename(path)
    return path
