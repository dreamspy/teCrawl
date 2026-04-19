import time
from typing import NamedTuple

import requests

from . import cache, config


class Release(NamedTuple):
    title: str
    artist: str
    label: str | None
    label_id: int | None
    artist_id: int | None
    release_id: int


_LAST_REQUEST = [0.0]
_MIN_INTERVAL = 1.05  # Discogs limit: 60 req/min authenticated


def _throttle() -> None:
    elapsed = time.time() - _LAST_REQUEST[0]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _LAST_REQUEST[0] = time.time()


def _get(url: str, params: dict | None = None) -> dict:
    cached = cache.get(url, params)
    if cached is not None:
        return cached
    _throttle()
    headers = {
        "Authorization": f"Discogs token={config.DISCOGS_TOKEN}",
        "User-Agent": config.USER_AGENT,
    }
    r = requests.get(url, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    cache.put(url, params, data)
    return data


def search_release(artist: str, title: str) -> Release | None:
    data = _get(
        "https://api.discogs.com/database/search",
        params={
            "artist": artist,
            "track": title,
            "type": "release",
            "per_page": 5,
        },
    )
    results = data.get("results", [])
    if not results:
        data = _get(
            "https://api.discogs.com/database/search",
            params={"q": f"{artist} {title}", "type": "release", "per_page": 5},
        )
        results = data.get("results", [])
    if not results:
        return None

    rid = results[0].get("id")
    if not rid:
        return None

    detail = _get(f"https://api.discogs.com/releases/{rid}")
    labels = detail.get("labels", []) or []
    artists = detail.get("artists", []) or []
    return Release(
        title=detail.get("title", ""),
        artist=artists[0]["name"] if artists else artist,
        label=labels[0].get("name") if labels else None,
        label_id=labels[0].get("id") if labels else None,
        artist_id=artists[0].get("id") if artists else None,
        release_id=rid,
    )


def label_releases(label_id: int, per_page: int = 50) -> list[dict]:
    data = _get(
        f"https://api.discogs.com/labels/{label_id}/releases",
        params={"per_page": per_page, "sort": "year", "sort_order": "desc"},
    )
    return data.get("releases", [])


def artist_releases(artist_id: int, per_page: int = 30) -> list[dict]:
    data = _get(
        f"https://api.discogs.com/artists/{artist_id}/releases",
        params={"per_page": per_page, "sort": "year", "sort_order": "desc"},
    )
    return data.get("releases", [])
