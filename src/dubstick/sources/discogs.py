from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from dubstick.sources.http import RateLimiter, client, get_json

API = "https://api.discogs.com"

# Authenticated clients get 60 requests per minute.
_limiter = RateLimiter(1.05)

# Discogs disambiguates homonyms as "Name (2)".
_DISAMBIGUATION = re.compile(r"\s+\(\d+\)$")
_SIDE = re.compile(r"^([A-Za-z]+)")


def clean_name(name: str) -> str:
    return _DISAMBIGUATION.sub("", name.strip().rstrip("*"))


def join_artists(artists: list[dict]) -> str:
    parts = []
    for artist in artists:
        name = clean_name(artist.get("anv") or artist.get("name", ""))
        join = (artist.get("join") or "").strip()
        parts.append(name)
        if join:
            parts.append(f" {join} " if join != "," else ", ")
    return "".join(parts).strip(" ,&")


def parse_duration(text: str | None) -> int | None:
    if not text:
        return None
    seconds = 0
    try:
        for part in text.strip().split(":"):
            seconds = seconds * 60 + int(part)
    except ValueError:
        return None
    return seconds or None


def side_of(position: str) -> str:
    match = _SIDE.match(position.strip())
    return match.group(1).upper() if match else "A"


@dataclass(frozen=True)
class SearchResult:
    id: int
    title: str
    year: str
    country: str
    formats: list[str]
    label: str
    catno: str
    thumb: str

    @property
    def is_vinyl(self) -> bool:
        return "Vinyl" in self.formats


@dataclass(frozen=True)
class Track:
    position: str
    side: str
    seq: int
    title: str
    artists: str
    duration_s: int | None


@dataclass
class Release:
    id: int
    master_id: int | None
    artist: str
    title: str
    label: str
    catno: str
    year: int | None
    country: str
    format: str
    thumb: str
    tracks: list[Track] = field(default_factory=list)
    videos: list[dict] = field(default_factory=list)


def _flatten_tracklist(tracklist: list[dict]) -> list[dict]:
    """Headings are dropped; an index track is replaced by its sub-tracks (A1a, A1b…)."""
    flat = []
    for item in tracklist:
        kind = item.get("type_", "track")
        if kind == "heading":
            continue
        if kind == "index" or item.get("sub_tracks"):
            for sub in item.get("sub_tracks") or []:
                flat.append({**sub, "artists": sub.get("artists") or item.get("artists")})
            continue
        flat.append(item)
    return flat


def _describe_format(formats: list[dict]) -> str:
    parts = []
    for fmt in formats:
        qty = fmt.get("qty", "1")
        prefix = f"{qty}× " if qty not in ("1", "", None) else ""
        details = ", ".join(fmt.get("descriptions") or [])
        parts.append(f"{prefix}{fmt.get('name', '')}" + (f" ({details})" if details else ""))
    return "; ".join(parts)


class Discogs:
    def __init__(self, token: str | None):
        self.token = token

    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Discogs token={self.token}"} if self.token else {}
        return client(headers=headers)

    async def search(self, *, barcode: str = "", catno: str = "", query: str = "") -> list[SearchResult]:
        params = {"type": "release", "per_page": 50}
        if barcode:
            params["barcode"] = re.sub(r"\D", "", barcode)
        if catno:
            params["catno"] = catno.strip()
        if query:
            params["q"] = query.strip()

        async with self._client() as http:
            data = await get_json(http, _limiter, f"{API}/database/search", params)

        results = [
            SearchResult(
                id=item["id"],
                title=item.get("title", ""),
                year=str(item.get("year") or ""),
                country=item.get("country", ""),
                formats=item.get("format") or [],
                label=(item.get("label") or [""])[0],
                catno=item.get("catno", ""),
                thumb=item.get("thumb", ""),
            )
            for item in data.get("results", [])
        ]
        # Vinyl pressings first, keeping Discogs' relevance order otherwise.
        return sorted(results, key=lambda r: not r.is_vinyl)

    async def release(self, release_id: int) -> Release:
        async with self._client() as http:
            data = await get_json(http, _limiter, f"{API}/releases/{release_id}")

        artist = join_artists(data.get("artists") or [])
        labels = data.get("labels") or [{}]

        tracks: list[Track] = []
        seq_by_side: dict[str, int] = {}
        for item in _flatten_tracklist(data.get("tracklist") or []):
            position = (item.get("position") or "").strip()
            side = side_of(position)
            seq_by_side[side] = seq_by_side.get(side, 0) + 1
            tracks.append(
                Track(
                    position=position or f"{side}{seq_by_side[side]}",
                    side=side,
                    seq=seq_by_side[side],
                    title=item.get("title", ""),
                    artists=join_artists(item.get("artists") or []) or artist,
                    duration_s=parse_duration(item.get("duration")),
                )
            )

        images = data.get("images") or [{}]
        return Release(
            id=data["id"],
            master_id=data.get("master_id"),
            artist=artist,
            title=data.get("title", ""),
            label=clean_name(labels[0].get("name", "")),
            catno=labels[0].get("catno", ""),
            year=data.get("year") or None,
            country=data.get("country", ""),
            format=_describe_format(data.get("formats") or []),
            thumb=data.get("thumb") or images[0].get("uri150", ""),
            tracks=tracks,
            videos=data.get("videos") or [],
        )
