from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from rapidfuzz import fuzz

# Downloads go one at a time with a pause in between, so YouTube sees a person, not a crawler.
_download_lock = asyncio.Lock()
PAUSE_BETWEEN_DOWNLOADS_S = 5.0


class YouTubeError(Exception):
    pass


def pick_video(videos: list[dict], title: str, duration_s: int | None) -> dict | None:
    """The Discogs-linked video whose title best names this track; never a YouTube search."""
    best, best_score = None, 0.0
    for video in videos:
        score = fuzz.partial_ratio(title.lower(), (video.get("title") or "").lower())
        if duration_s and video.get("duration"):
            # Full-release uploads are much longer than one track; skip them.
            if video["duration"] > duration_s * 1.5 + 30:
                continue
            score -= min(abs(video["duration"] - duration_s), 90) / 3
        if score > best_score:
            best, best_score = video, score
    return best if best_score >= 70 else None


def _stem(url: str) -> str:
    return "youtube-" + hashlib.sha1(url.encode()).hexdigest()[:16]


def _cached(url: str, cache_dir: Path) -> Path | None:
    found = [p for p in cache_dir.glob(f"{_stem(url)}.*") if p.suffix != ".part"]
    return found[0] if found else None


def _download(url: str, cache_dir: Path) -> Path:
    # Imported lazily: yt-dlp is heavy and only needed when a track has no local file.
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    stem = _stem(url)

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(cache_dir / f"{stem}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    # Only needed if YouTube starts asking this host to sign in.
    cookies = os.environ.get("DUBSTICK_YT_COOKIES")
    if cookies:
        options["cookiefile"] = cookies

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            return Path(ydl.prepare_filename(info))
    except DownloadError as exc:
        # yt-dlp prefixes its messages with "ERROR: "; keep the human part.
        raise YouTubeError(str(exc).removeprefix("ERROR: ").strip()) from exc


async def download_audio(url: str, cache_dir: Path) -> Path:
    cached = _cached(url, cache_dir)
    if cached:
        return cached
    async with _download_lock:
        try:
            return await asyncio.to_thread(_download, url, cache_dir)
        finally:
            await asyncio.sleep(PAUSE_BETWEEN_DOWNLOADS_S)
