from __future__ import annotations

import asyncio
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import httpx

from dubstick import camelot, library, records
from dubstick.analysis import analyse_file
from dubstick.config import Config
from dubstick.db import Database
from dubstick.events import EventBus
from dubstick.settings import Settings
from dubstick.sources import deezer, musicbrainz, youtube
from dubstick.sources.discogs import Discogs

log = logging.getLogger(__name__)

# Releases analysed at the same time; each one fans out over its tracks.
RELEASE_CONCURRENCY = 2

# The music library is re-indexed at startup and then this often; only changed files are re-read.
REINDEX_EVERY_S = 6 * 3600


class Worker:
    def __init__(self, config: Config, db: Database, bus: EventBus):
        self.config = config
        self.db = db
        self.bus = bus
        self.discogs = Discogs(config.discogs_token)
        self._wake = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._active: set[int] = set()
        self._pool: ProcessPoolExecutor | None = None
        self._runner: asyncio.Task | None = None
        self._indexer: asyncio.Task | None = None
        self.indexing = False

    def _new_pool(self) -> ProcessPoolExecutor:
        # "spawn" keeps the analysis processes clear of the event loop's threads and sockets.
        return ProcessPoolExecutor(
            max_workers=self.config.essentia_workers,
            mp_context=multiprocessing.get_context("spawn"),
        )

    async def start(self) -> None:
        self._pool = self._new_pool()
        # Work interrupted by a restart goes back into the queue.
        self.db.execute("UPDATE release SET status = 'queued' WHERE status = 'analyzing'")
        self._runner = asyncio.create_task(self._run())
        self._indexer = asyncio.create_task(self._index_periodically())

    async def stop(self) -> None:
        for task in (self._runner, self._indexer):
            if task:
                task.cancel()
        for task in self._tasks:
            task.cancel()
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def wake(self) -> None:
        self._wake.set()

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _run(self) -> None:
        while True:
            free = RELEASE_CONCURRENCY - len(self._active)
            if free > 0:
                for row in self.db.query(
                    "SELECT id FROM release WHERE status = 'queued' ORDER BY updated_at LIMIT ?", (free,)
                ):
                    self._active.add(row["id"])
                    self._spawn(self._process(row["id"]))

            self._wake.clear()
            try:
                # Poll occasionally anyway, in case a wake-up was missed.
                await asyncio.wait_for(self._wake.wait(), timeout=30)
            except TimeoutError:
                pass

    def _notify(self, record_id: int) -> None:
        self.bus.publish("release", id=record_id)

    def _progress(self, record_id: int, text: str) -> None:
        records.set_progress(self.db, record_id, text)
        self._notify(record_id)

    async def _process(self, record_id: int) -> None:
        try:
            records.set_status(self.db, record_id, "analyzing", progress="fetching Discogs release")
            self._notify(record_id)
            await self._analyse(record_id)
        except Exception as exc:
            log.exception("analysis of release %s failed", record_id)
            records.set_status(self.db, record_id, "failed", error=_describe(exc))
        finally:
            self._active.discard(record_id)
            self._notify(record_id)
            self.wake()

    async def _analyse(self, record_id: int) -> None:
        row = self.db.one("SELECT discogs_release_id FROM release WHERE id = ?", (record_id,))
        release = await self.discogs.release(row["discogs_release_id"])
        records.save_release(self.db, record_id, release)
        self._notify(record_id)

        self._progress(record_id, "looking for local files")
        local_files = await self._local_album(release)
        if local_files:
            self.db.execute("UPDATE release SET local_album = ? WHERE id = ?", (str(Path(local_files[0].path).parent), record_id))

        tracks = self.db.query(
            "SELECT id, title, artists, duration_s FROM track WHERE release_id = ? ORDER BY id", (record_id,)
        )
        done = 0

        async def one_track(track) -> None:
            nonlocal done
            await self._analyse_track(track, local_files, release.videos)
            done += 1
            self._progress(record_id, f"analysed {done}/{len(tracks)} tracks")

        await asyncio.gather(*(one_track(track) for track in tracks))

        settings = Settings.load(self.db)
        status = records.refresh_finals(self.db, record_id, settings.bpm_min, settings.bpm_max)
        records.set_status(self.db, record_id, status)

    async def _local_album(self, release) -> list[library.LocalTrack]:
        try:
            groups, releases = await musicbrainz.release_group_ids(release.master_id, release.id)
        except httpx.HTTPError as exc:
            log.warning("MusicBrainz lookup failed: %s", exc)
            groups, releases = set(), set()
        return library.find_album(
            self.db, releasegroup_ids=groups, release_ids=releases, artist=release.artist, album=release.title
        )

    async def _analyse_track(self, track, local_files: list[library.LocalTrack], videos: list[dict]) -> None:
        self._set_note(track["id"], None)
        # The preview is analysed for every track: it cross-checks whichever full track is used.
        preview = asyncio.create_task(self._analyse_preview(track))

        local = library.match_track(track["title"], track["duration_s"], local_files)
        if local:
            self.db.execute("UPDATE track SET local_path = ? WHERE id = ?", (local.path, track["id"]))
            if local.bpm_tag:
                records.save_opinion(self.db, track["id"], "file_tag", bpm=local.bpm_tag, camelot=None)
            await self._run_essentia(track["id"], "essentia_local", local.path)
        else:
            try:
                await self._analyse_youtube(track, videos)
            except (LookupError, RuntimeError, youtube.YouTubeError) as exc:
                self._set_note(track["id"], str(exc))

        await preview

    def _set_note(self, track_id: int, note: str | None) -> None:
        self.db.execute("UPDATE track SET note = ? WHERE id = ?", (note, track_id))

    async def _analyse_preview(self, track) -> None:
        try:
            preview = await deezer.find_preview(track["artists"], track["title"], track["duration_s"])
            if preview is None:
                return
            path = await deezer.download(preview, self.config.cache_dir)
        except httpx.HTTPError as exc:
            log.warning("Deezer preview failed for %s: %s", track["title"], exc)
            return
        await self._run_essentia(
            track["id"], "essentia_deezer", str(path), extra={"deezer_id": preview.deezer_id}, discard=True
        )

    async def _analyse_youtube(self, track, videos: list[dict]) -> str:
        """Analyse the Discogs-linked video for this track. Raises with a readable reason on failure."""
        video = youtube.pick_video(videos, track["title"], track["duration_s"])
        if video is None:
            raise LookupError("No YouTube video linked on Discogs matches this track.")

        name = video.get("title") or video["uri"]
        try:
            path = await youtube.download_audio(video["uri"], self.config.cache_dir)
        except youtube.YouTubeError as exc:
            raise youtube.YouTubeError(f"YouTube refused “{name}”: {exc}") from exc

        error = await self._run_essentia(
            track["id"], "essentia_youtube", str(path), extra={"video": video["uri"]}, discard=True
        )
        if error:
            raise RuntimeError(f"Analysis of “{name}” failed: {error}")
        return name

    async def _run_essentia(
        self, track_id: int, source: str, path: str, *, extra: dict | None = None, discard: bool = False
    ) -> str | None:
        """Analyse a file and store the opinion. Returns an error message on failure.

        Downloaded and uploaded audio is discarded afterwards; only the result is kept.
        """
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(self._pool, analyse_file, path)
        except BrokenProcessPool:
            # A crashed analysis process poisons the whole pool; start a fresh one.
            log.error("Essentia process crashed on %s, restarting the pool", path)
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = self._new_pool()
            return "the analysis process crashed"
        except Exception as exc:
            log.warning("Essentia failed on %s: %s", path, exc)
            return str(exc)
        finally:
            if discard:
                Path(path).unlink(missing_ok=True)

        records.save_opinion(
            self.db,
            track_id,
            source,
            bpm=result.bpm,
            camelot=camelot.from_key(result.key, result.scale),
            bpm_confidence=result.bpm_confidence,
            key_strength=result.key_strength,
            raw={**result.as_dict(), **(extra or {})},
        )
        return None

    def _track(self, track_id: int):
        track = self.db.one(
            "SELECT t.*, r.discogs_release_id, r.id AS record_id FROM track t JOIN release r ON r.id = t.release_id "
            "WHERE t.id = ?",
            (track_id,),
        )
        if track is None:
            raise LookupError("No such track.")
        return track

    async def analyse_youtube(self, track_id: int) -> str:
        """On-demand retry of a track's Discogs-linked YouTube video."""
        track = self._track(track_id)
        release = await self.discogs.release(track["discogs_release_id"])
        name = await self._analyse_youtube(track, release.videos)
        self._set_note(track_id, None)
        self._refresh(track["record_id"])
        return f"Analysed “{name}”."

    async def analyse_recording(self, track_id: int, path: Path) -> str:
        """Analyse audio recorded or uploaded for a track, then throw the audio away."""
        track = self._track(track_id)
        error = await self._run_essentia(track_id, "essentia_recording", str(path), discard=True)
        if error:
            raise RuntimeError(f"Could not analyse the recording: {error}")
        self._refresh(track["record_id"])
        return "Recording analysed."

    def _refresh(self, record_id: int) -> None:
        settings = Settings.load(self.db)
        with records.watching_label(self.db, record_id):
            records.refresh_finals(self.db, record_id, settings.bpm_min, settings.bpm_max)
        records.set_status(self.db, record_id, records.review_status(self.db, record_id))
        self._notify(record_id)

    async def _index_periodically(self) -> None:
        if not self.config.music_roots:
            return
        while True:
            try:
                updated, removed = await self.reindex_library()
                log.info("library index: %d files updated, %d removed", updated, removed)
            except OSError as exc:
                # A disk that went away is not a reason to stop indexing the others next time.
                log.warning("library index failed: %s", exc)
            await asyncio.sleep(REINDEX_EVERY_S)

    async def reindex_library(self) -> tuple[int, int]:
        if self.indexing:
            return 0, 0
        self.indexing = True
        self.bus.publish("library", indexing=True)
        try:
            return await asyncio.to_thread(library.reindex, self.db, self.config.music_roots)
        finally:
            self.indexing = False
            self.bus.publish("library", indexing=False)


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{exc.request.url.host} answered {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return f"network error: {exc}"
    return f"{type(exc).__name__}: {exc}"
