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


def _clean_artist(artist: str) -> str:
    """Discogs disambiguates same-name artists with suffixes like 'Feral (11)'
    (numeric ID) and 'Prodigy*' (alias marker). Spotify search trips on both —
    'Prodigy*' returns nothing, 'Feral (11)' returns nothing, but stripping the
    suffix lets the canonical artist match. We keep the cleaned version as a
    fallback only — if the original matches first, we prefer that."""
    cleaned = re.sub(r"\s*\(\d+\)\s*$", "", artist)  # 'Feral (11)' -> 'Feral'
    cleaned = re.sub(r"\*+\s*$", "", cleaned)         # 'Prodigy*' -> 'Prodigy'
    return cleaned.strip()


_VOL_RE = re.compile(
    r"\s+(?:Vol|Vol\.|Volum|Volume|Part|Pt|Pt\.)\s+\d+\s*$",
    re.IGNORECASE,
)
_FORMAT_RE = re.compile(
    r"\s+(?:EP|LP|Single|Album|Maxi)\s*$",
    re.IGNORECASE,
)


def _clean_title(title: str) -> str:
    """Strip Discogs-isms that Spotify doesn't carry in its release titles:
    parenthetical version notes ('(Shackleton Mixes)', '(X30)'), trailing
    volume markers ('Volum 2', 'Vol. 3', 'Part 1'), and trailing format
    markers ('EP', 'LP') without a dash separator (Discogs writes
    'Woke Up Right Handed EP', Spotify lists it as 'Woke Up Right Handed')."""
    cleaned = re.sub(r"\s*\([^)]*\)\s*", " ", title).strip()
    cleaned = _VOL_RE.sub("", cleaned)
    cleaned = _FORMAT_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


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


def _search_track_once(artist: str, title: str) -> Track | None:
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


def _search_album_once(artist: str, title: str) -> Album | None:
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


def _query_variants(artist: str, title: str) -> list[tuple[str, str]]:
    """Original first (cheapest, most accurate when it works); then progressively
    cleaner artist/title combinations for Discogs noise. Deduped, original order
    preserved so we stop at the first hit."""
    variants = [(artist, title)]
    a_clean = _clean_artist(artist)
    t_clean = _clean_title(title)
    if a_clean != artist:
        variants.append((a_clean, title))
    if t_clean != title:
        variants.append((artist, t_clean))
    if a_clean != artist and t_clean != title:
        variants.append((a_clean, t_clean))
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for v in variants:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def search_track(artist: str, title: str) -> Track | None:
    for a, t in _query_variants(artist, title):
        hit = _search_track_once(a, t)
        if hit:
            return hit
    return None


def search_album(artist: str, title: str) -> Album | None:
    for a, t in _query_variants(artist, title):
        hit = _search_album_once(a, t)
        if hit:
            return hit
    # Album search exhausted — Discogs sometimes returns single-track releases
    # that exist on Spotify only as a track. Fall back to track search and
    # wrap the result as an Album-shaped hit (the renderer treats type="album"
    # vs type="track" via Candidate.spotify_type, set by the caller, so we
    # return None here and let the caller decide). Keeping this comment as a
    # marker for the next iteration if the parens/asterisk fixes don't move
    # the needle enough.
    return None


_PLAYLIST_URL_RE = re.compile(r"open\.spotify\.com/playlist/[a-zA-Z0-9]+")


def looks_like_playlist_url(s: str) -> bool:
    return bool(_PLAYLIST_URL_RE.search(s))
