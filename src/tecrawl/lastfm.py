import time

import requests

from . import cache, config

BASE_URL = "https://ws.audioscrobbler.com/2.0/"

_LAST_REQUEST = [0.0]
_MIN_INTERVAL = 0.25  # 5 req/sec


def _throttle() -> None:
    elapsed = time.time() - _LAST_REQUEST[0]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _LAST_REQUEST[0] = time.time()


def _get(method: str, **params) -> dict:
    full_params = {
        "method": method,
        "api_key": config.LASTFM_API_KEY,
        "format": "json",
        **params,
    }
    cached = cache.get(BASE_URL, full_params)
    if cached is not None:
        return cached
    _throttle()
    r = requests.get(
        BASE_URL,
        params=full_params,
        headers={"User-Agent": config.USER_AGENT},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    cache.put(BASE_URL, full_params, data)
    return data


def similar_tracks(artist: str, track: str, limit: int = 10) -> list[tuple[str, str]]:
    data = _get("track.getsimilar", artist=artist, track=track, limit=limit)
    tracks = data.get("similartracks", {}).get("track", [])
    return [
        (t["artist"]["name"], t["name"])
        for t in tracks
        if t.get("artist", {}).get("name") and t.get("name")
    ]


def similar_artists(artist: str, limit: int = 5) -> list[str]:
    data = _get("artist.getsimilar", artist=artist, limit=limit)
    artists = data.get("similarartists", {}).get("artist", [])
    return [a["name"] for a in artists if a.get("name")]


def artist_top_tracks(artist: str, limit: int = 3) -> list[tuple[str, str]]:
    data = _get("artist.gettoptracks", artist=artist, limit=limit)
    tracks = data.get("toptracks", {}).get("track", [])
    return [
        (t["artist"]["name"], t["name"])
        for t in tracks
        if t.get("artist", {}).get("name") and t.get("name")
    ]
