"""Resolve an artist+title to a YouTube video ID via the public search page.
No API key (and therefore no Data API quota — the user runs more than 100
searches/day). YouTube embeds its result list as JSON inside the HTML; we
pull the first 11-char videoId. Cached aggressively so re-runs are free."""

import re
import time

import requests

from . import cache

_LAST_REQUEST = [0.0]
# YouTube tolerates a fast pace from a normal-UA browser. 0.3s gives
# ~3 req/sec which is well below anything that would draw rate-limit attention.
_MIN_INTERVAL = 0.3

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)

# Video IDs are always exactly 11 chars from this alphabet.
_VIDEO_ID_RE = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')


def _throttle() -> None:
    elapsed = time.time() - _LAST_REQUEST[0]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _LAST_REQUEST[0] = time.time()


def search_video_id(artist: str, title: str) -> str | None:
    url = "https://www.youtube.com/results"
    params = {"search_query": f"{artist} {title}"}
    cached = cache.get(url, params)
    if cached is not None:
        return cached.get("video_id")

    _throttle()
    try:
        r = requests.get(
            url,
            params=params,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=15,
        )
        r.raise_for_status()
        m = _VIDEO_ID_RE.search(r.text)
        video_id = m.group(1) if m else None
    except Exception:
        video_id = None

    # Cache negatives too so we don't keep retrying for queries that genuinely
    # have no good match (rare on YouTube but possible).
    cache.put(url, params, {"video_id": video_id})
    return video_id


def video_info(video_id: str) -> dict | None:
    """Title + channel name for a video via the no-key oEmbed endpoint.
    Returns {'title': …, 'author': …}, or None if the video is private,
    removed, or the lookup failed."""
    url = "https://www.youtube.com/oembed"
    params = {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}
    cached = cache.get(url, params)
    if cached is not None:
        return cached.get("info")

    _throttle()
    try:
        r = requests.get(url, params=params, headers={"User-Agent": _UA}, timeout=15)
        if r.status_code in (400, 401, 403, 404):
            info = None  # video gone/private — a definitive miss, cacheable
        else:
            r.raise_for_status()
            j = r.json()
            info = {
                "title": j.get("title") or "",
                "author": j.get("author_name") or "",
            }
    except Exception:
        return None  # transient failure — don't cache

    cache.put(url, params, {"info": info})
    return info
