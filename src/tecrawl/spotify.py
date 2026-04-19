import re
import time
import unicodedata
from typing import NamedTuple

import requests

from . import cache, config


class Track(NamedTuple):
    artist: str
    title: str
    spotify_id: str | None


class Album(NamedTuple):
    artist: str
    title: str
    spotify_id: str | None


_TOKEN: dict = {}


def _get_token() -> str:
    """Client credentials token. Used for /search (no user context needed)."""
    if _TOKEN.get("token") and _TOKEN.get("expires_at", 0) > time.time() + 30:
        return _TOKEN["token"]
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET),
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    _TOKEN["token"] = payload["access_token"]
    _TOKEN["expires_at"] = time.time() + payload.get("expires_in", 3600)
    return _TOKEN["token"]


def _get(url: str, params: dict | None = None) -> dict:
    cached = cache.get(url, params)
    if cached is not None:
        return cached
    headers = {
        "Authorization": f"Bearer {_get_token()}",
        "User-Agent": config.USER_AGENT,
    }
    r = requests.get(url, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    cache.put(url, params, data)
    return data


def _normalize(s: str) -> str:
    """Lowercase, strip diacritics + non-alphanumerics for fuzzy comparison."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _artists_match(requested: str, returned: str) -> bool:
    r = _normalize(requested)
    rt = _normalize(returned)
    if not r or not rt:
        return False
    return r in rt or rt in r


def search_track(artist: str, title: str) -> Track | None:
    query = f'artist:"{artist}" track:"{title}"'
    data = _get(
        "https://api.spotify.com/v1/search",
        params={"q": query, "type": "track", "limit": 5},
    )
    items = data.get("tracks", {}).get("items", [])
    if not items:
        data = _get(
            "https://api.spotify.com/v1/search",
            params={"q": f"{artist} {title}", "type": "track", "limit": 5},
        )
        items = data.get("tracks", {}).get("items", [])
    for t in items:
        track_artists = [a.get("name", "") for a in t.get("artists", [])]
        if any(_artists_match(artist, ta) for ta in track_artists):
            return Track(
                artist=", ".join(track_artists),
                title=t["name"],
                spotify_id=t["id"],
            )
    return None


def search_album(artist: str, title: str) -> Album | None:
    query = f'artist:"{artist}" album:"{title}"'
    data = _get(
        "https://api.spotify.com/v1/search",
        params={"q": query, "type": "album", "limit": 5},
    )
    items = data.get("albums", {}).get("items", [])
    if not items:
        data = _get(
            "https://api.spotify.com/v1/search",
            params={"q": f"{artist} {title}", "type": "album", "limit": 5},
        )
        items = data.get("albums", {}).get("items", [])
    for a in items:
        album_artists = [x.get("name", "") for x in a.get("artists", [])]
        if any(_artists_match(artist, aa) for aa in album_artists):
            return Album(
                artist=", ".join(album_artists),
                title=a["name"],
                spotify_id=a["id"],
            )
    return None


_PLAYLIST_URL_RE = re.compile(r"open\.spotify\.com/playlist/[a-zA-Z0-9]+")


def looks_like_playlist_url(s: str) -> bool:
    return bool(_PLAYLIST_URL_RE.search(s))
