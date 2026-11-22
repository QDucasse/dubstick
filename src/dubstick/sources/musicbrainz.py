from __future__ import annotations

import httpx

from dubstick.sources.http import RateLimiter, client, get_json

API = "https://musicbrainz.org/ws/2"

# MusicBrainz asks clients to stay at or below one request per second.
_limiter = RateLimiter(1.1)


async def _url_relations(http: httpx.AsyncClient, resource: str, inc: str) -> list[dict]:
    try:
        data = await get_json(http, _limiter, f"{API}/url", {"resource": resource, "inc": inc, "fmt": "json"})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return []
        raise
    return data.get("relations") or []


async def release_group_ids(discogs_master_id: int | None, discogs_release_id: int) -> tuple[set[str], set[str]]:
    """MusicBrainz release-group and release IDs linked to a Discogs master or release."""
    groups: set[str] = set()
    releases: set[str] = set()

    async with client() as http:
        if discogs_master_id:
            for rel in await _url_relations(
                http, f"https://www.discogs.com/master/{discogs_master_id}", "release-group-rels"
            ):
                if group := rel.get("release_group", {}).get("id"):
                    groups.add(group)

        for rel in await _url_relations(
            http, f"https://www.discogs.com/release/{discogs_release_id}", "release-rels"
        ):
            if release := rel.get("release", {}).get("id"):
                releases.add(release)

        if not groups:
            # Only the pressing is linked: climb up to its release group.
            for release in list(releases)[:3]:
                data = await get_json(http, _limiter, f"{API}/release/{release}", {"inc": "release-groups", "fmt": "json"})
                if group := data.get("release-group", {}).get("id"):
                    groups.add(group)

    return groups, releases
