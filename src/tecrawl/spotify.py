import json
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
    youtube_id: str | None = None


class Album(NamedTuple):
    artist: str
    title: str
    spotify_id: str | None


_TOKEN: dict = {}

# One failed credential check (or a punitive 429) parks Spotify for a while
# so a 20-candidate resolution doesn't hammer a dead endpoint — this account
# previously earned a 21-hour Retry-After from exactly that kind of loop.
_UNAVAILABLE: dict = {}

_LAST_REQUEST = [0.0]
_MIN_INTERVAL = 0.5  # self-imposed ~120 req/min, far under punitive-backoff pace
_MAX_RETRY_AFTER = 60  # sleep-and-retry short 429s; park anything longer


class SpotifyUnavailable(Exception):
    """The Spotify Web API is unusable right now (bad credentials or rate
    limit). Callers degrade to search links instead of retrying."""


def _mark_unavailable(reason: str, seconds: float) -> None:
    _UNAVAILABLE["until"] = time.time() + seconds
    _UNAVAILABLE["reason"] = reason


def unavailable_reason() -> str | None:
    if _UNAVAILABLE.get("until", 0) > time.time():
        return _UNAVAILABLE["reason"]
    return None


def _throttle() -> None:
    elapsed = time.time() - _LAST_REQUEST[0]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _LAST_REQUEST[0] = time.time()


def _retry_after(r: requests.Response) -> float:
    try:
        return float(r.headers.get("Retry-After", 2))
    except (TypeError, ValueError):
        return 2.0


def _get_token() -> str:
    """Client credentials token. Used for /search (no user context needed)."""
    if _TOKEN.get("token") and _TOKEN.get("expires_at", 0) > time.time() + 30:
        return _TOKEN["token"]
    reason = unavailable_reason()
    if reason:
        raise SpotifyUnavailable(reason)
    if not (config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET):
        _mark_unavailable("Spotify credentials missing from .env", 600)
        raise SpotifyUnavailable(_UNAVAILABLE["reason"])
    _throttle()
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET),
        timeout=15,
    )
    if r.status_code == 429:
        wait = _retry_after(r)
        _mark_unavailable(f"Spotify rate-limited (retry in {wait:.0f}s)", wait)
        raise SpotifyUnavailable(_UNAVAILABLE["reason"])
    if r.status_code in (400, 401, 403):
        # Dead/deleted app. Memoized so we fail fast once per 10 minutes
        # instead of re-POSTing per candidate; re-probes in case .env is fixed.
        _mark_unavailable(
            "Spotify app credentials rejected — recreate the app and update .env",
            600,
        )
        raise SpotifyUnavailable(_UNAVAILABLE["reason"])
    r.raise_for_status()
    payload = r.json()
    _TOKEN["token"] = payload["access_token"]
    _TOKEN["expires_at"] = time.time() + payload.get("expires_in", 3600)
    return _TOKEN["token"]


def _get(url: str, params: dict | None = None) -> dict:
    cached = cache.get(url, params)
    if cached is not None:
        return cached
    token = _get_token()  # raises SpotifyUnavailable while parked
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": config.USER_AGENT,
    }
    for attempt in (1, 2):
        _throttle()
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code == 429:
            wait = _retry_after(r)
            if attempt == 1 and wait <= _MAX_RETRY_AFTER:
                time.sleep(wait)
                continue
            _mark_unavailable(f"Spotify rate-limited (Retry-After {wait:.0f}s)", wait)
            raise SpotifyUnavailable(_UNAVAILABLE["reason"])
        r.raise_for_status()
        data = r.json()
        cache.put(url, params, data)
        return data
    raise SpotifyUnavailable("unreachable")  # loop always returns or raises


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


def search_track_freetext(q: str) -> Track | None:
    """Free-text track search, no artist sanity check. Used to resolve the
    user's own typed query (they know what they meant), never for scraped
    candidates."""
    data = _get(
        "https://api.spotify.com/v1/search",
        params={"q": q, "type": "track", "limit": 1},
    )
    items = data.get("tracks", {}).get("items", [])
    if not items:
        return None
    t = items[0]
    names = [a.get("name", "") for a in t.get("artists", []) if a.get("name")]
    if not (names and t.get("name")):
        return None
    return Track(artist=", ".join(names), title=t["name"], spotify_id=t.get("id"))


_EMBED_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)


def _embed_entity(kind: str, spotify_id: str) -> dict | None:
    """No-auth metadata fallback: open.spotify.com/embed/<kind>/<id> is
    server-rendered with the entity JSON inline (the iframe player needs it),
    so pasted links resolve even while the API app is dead."""
    url = f"https://open.spotify.com/embed/{kind}/{spotify_id}"
    cached = cache.get(url, None)
    if cached is not None:
        return cached.get("entity")
    try:
        r = requests.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=15)
        r.raise_for_status()
        m = _EMBED_NEXT_DATA_RE.search(r.text)
        entity = None
        if m:
            blob = json.loads(m.group(1))
            entity = (
                blob.get("props", {})
                .get("pageProps", {})
                .get("state", {})
                .get("data", {})
                .get("entity")
            )
        if not isinstance(entity, dict):
            entity = None
    except Exception:
        return None  # transient failure — don't cache
    cache.put(url, None, {"entity": entity})
    return entity


def _join_artist_names(items: list) -> str:
    return ", ".join(a.get("name", "") for a in items if a.get("name"))


def get_track(track_id: str) -> Track | None:
    """Resolve a pasted track link to (artist, title). Web API first, embed
    page as the no-auth fallback."""
    try:
        data = _get(f"https://api.spotify.com/v1/tracks/{track_id}")
        artist = _join_artist_names(data.get("artists", []))
        if artist and data.get("name"):
            return Track(artist=artist, title=data["name"], spotify_id=track_id)
    except Exception:
        pass
    entity = _embed_entity("track", track_id)
    if not entity:
        return None
    artist = _join_artist_names(entity.get("artists", []))
    title = entity.get("name") or entity.get("title") or ""
    if not (artist and title):
        return None
    return Track(artist=artist, title=title, spotify_id=track_id)


def get_album_seed(album_id: str) -> tuple[Track, str] | None:
    """An album link seeds from the album's first track. Returns
    (track, album_title), or None if the album can't be read at all."""
    try:
        data = _get(f"https://api.spotify.com/v1/albums/{album_id}")
        artist = _join_artist_names(data.get("artists", []))
        items = data.get("tracks", {}).get("items", [])
        if artist and items and items[0].get("name"):
            first = items[0]
            return (
                Track(artist=artist, title=first["name"], spotify_id=first.get("id")),
                data.get("name", ""),
            )
    except Exception:
        pass
    entity = _embed_entity("album", album_id)
    if not entity:
        return None
    # Album embed entities carry the artist as a plain `subtitle` string
    # (track entities have an `artists` list — albums don't).
    artist = _join_artist_names(entity.get("artists", [])) or (
        entity.get("subtitle") or ""
    ).strip()
    if not artist:
        return None
    album_title = entity.get("name") or entity.get("title") or ""
    track_list = entity.get("trackList") or []
    if isinstance(track_list, list) and track_list:
        first = track_list[0]
        title = first.get("title") or first.get("name") or ""
        uri = first.get("uri") or ""
        first_id = uri.rsplit(":", 1)[-1] if uri.startswith("spotify:track:") else None
        if title:
            first_artist = (first.get("subtitle") or "").strip() or artist
            return Track(artist=first_artist, title=title, spotify_id=first_id), album_title
    if album_title:
        # Last resort: seed on the album title itself. Discogs matches release
        # titles fine; the Last.fm track-similar angle just won't fire.
        return Track(artist=artist, title=album_title, spotify_id=None), album_title
    return None


_PLAYLIST_URL_RE = re.compile(r"open\.spotify\.com/playlist/[a-zA-Z0-9]+")


def looks_like_playlist_url(s: str) -> bool:
    return bool(_PLAYLIST_URL_RE.search(s))
