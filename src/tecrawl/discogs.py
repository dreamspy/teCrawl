import time
import unicodedata
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
    styles: list[str]
    year: int | None
    # False when this is an artist-level stand-in rather than the seed
    # track's own release (see `search_release`). The Discogs angles still
    # run off it, but the page says so and the seed's DGS↗ link keeps
    # pointing at a search instead of a release that isn't the seed.
    exact: bool = True


_LAST_REQUEST = [0.0]
_MIN_INTERVAL = 1.05  # Discogs limit: 60 req/min authenticated


def release_id_from_stub(r: dict) -> int | None:
    """Discogs API returns a mix of 'release' and 'master' stubs in
    /artists/{id}/releases and /labels/{id}/releases. For 'release' stubs,
    `id` IS the release_id. For 'master' stubs, `id` is the master_id and
    the real release_id is in `main_release` — using `id` builds a URL
    that redirects to a completely unrelated release that happens to share
    the integer (Discogs masters and releases share an ID space)."""
    if r.get("type") == "master" and r.get("main_release"):
        return r.get("main_release")
    return r.get("id")


def _coerce_year(v) -> int | None:
    """Discogs sometimes returns year as int, sometimes as a string."""
    if v is None or v == "":
        return None
    try:
        y = int(str(v)[:4])
        return y if 1900 < y < 2100 else None
    except (TypeError, ValueError):
        return None


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


# Letters NFKD won't decompose (ø has no combining form, etc.) so "Rødhåd"
# can match a stub spelled "Rodhad".
_TRANSLIT = str.maketrans({
    "ø": "o", "đ": "d", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe",
    "ł": "l", "ß": "ss",
})


def _normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.casefold().translate(_TRANSLIT).strip()


def _pick_result(results: list[dict], artist: str) -> dict | None:
    """Best result whose artist actually matches the requested artist.
    Search stub titles are 'Artist - Title'; require the requested artist to
    appear in the artist segment (handles 'Burial (2)' disambiguation,
    'Burial / Four Tet' splits, trailing asterisks) — Discogs search is fuzzy
    enough that results[0] can be a completely unrelated artist's release,
    and a wrong anchor poisons the label, artist AND style angles.

    Among artist-matched stubs, prefer official releases on real labels:
    bootleg remixes ("Unofficial Release" on "Not On Label (X Self-released)")
    often outrank the actual record and would anchor the label angle on
    junk."""
    want = _normalize_name(artist)
    if not want:
        return None
    matched: list[tuple[tuple, dict]] = []
    for i, r in enumerate(results):
        artist_part = (r.get("title") or "").partition(" - ")[0]
        if want not in _normalize_name(artist_part):
            continue
        unofficial = "unofficial" in " ".join(r.get("format") or []).lower()
        labels = [str(l) for l in (r.get("label") or [])]
        no_real_label = not labels or all(
            l.lower().startswith("not on label") for l in labels
        )
        matched.append(((unofficial, no_real_label, i), r))
    if not matched:
        return None
    return min(matched)[1]


def _pick_artist_anchor(results: list[dict], artist: str) -> dict | None:
    """Best release by this artist to stand in as the anchor when the exact
    track can't be found. Same official/real-label preference as
    `_pick_result`, then most-owned first — the artist's most widely held
    record is the most representative read on their label and styles."""
    want = _normalize_name(artist)
    if not want:
        return None
    scored: list[tuple[tuple, dict]] = []
    for i, r in enumerate(results):
        artist_part = (r.get("title") or "").partition(" - ")[0]
        if want not in _normalize_name(artist_part):
            continue
        unofficial = "unofficial" in " ".join(r.get("format") or []).lower()
        labels = [str(l) for l in (r.get("label") or [])]
        no_real_label = not labels or all(
            l.lower().startswith("not on label") for l in labels
        )
        have = (r.get("community") or {}).get("have") or 0
        scored.append(((unofficial, no_real_label, -have, i), r))
    if not scored:
        return None
    return min(scored)[1]


def _fetch_release(rid: int, fallback_artist: str, *, exact: bool) -> Release | None:
    detail = _get(f"https://api.discogs.com/releases/{rid}")
    labels = detail.get("labels", []) or []
    artists = detail.get("artists", []) or []
    return Release(
        title=detail.get("title", ""),
        artist=artists[0]["name"] if artists else fallback_artist,
        label=labels[0].get("name") if labels else None,
        label_id=labels[0].get("id") if labels else None,
        artist_id=artists[0].get("id") if artists else None,
        release_id=rid,
        styles=list(detail.get("styles") or []),
        year=detail.get("year") or None,
        exact=exact,
    )


def search_release(artist: str, title: str) -> Release | None:
    """Anchor release for the Discogs angles. Exact track match first; if
    Discogs can't find the track, fall back to an artist-level anchor
    (`exact=False`) rather than returning None.

    The fallback matters more than it looks: Discogs' `track=` index only
    covers tracklists it has actually indexed, and the `q=` fallback matches
    release *titles*, so plenty of real album tracks match neither. Returning
    None there silently killed five of the seven discovery angles and left
    the page showing only Last.fm's similar artists."""
    data = _get(
        "https://api.discogs.com/database/search",
        params={
            "artist": artist,
            "track": title,
            "type": "release",
            "per_page": 5,
        },
    )
    hit = _pick_result(data.get("results", []), artist)
    if not hit:
        data = _get(
            "https://api.discogs.com/database/search",
            params={"q": f"{artist} {title}", "type": "release", "per_page": 5},
        )
        hit = _pick_result(data.get("results", []), artist)
    if hit and hit.get("id"):
        return _fetch_release(hit["id"], artist, exact=True)

    # No release matched the track. Anchor on the artist instead: the label,
    # label-mate, style and recommendation angles all still produce genuinely
    # relevant digs, they're just keyed off the artist rather than this one
    # track. Still artist-verified, so a wrong anchor stays impossible.
    data = _get(
        "https://api.discogs.com/database/search",
        params={"artist": artist, "type": "release", "per_page": 25},
    )
    anchor = _pick_artist_anchor(data.get("results", []), artist)
    if not anchor or not anchor.get("id"):
        return None
    return _fetch_release(anchor["id"], artist, exact=False)


def label_releases(label_id: int, per_page: int = 50, page: int = 1) -> list[dict]:
    data = _get(
        f"https://api.discogs.com/labels/{label_id}/releases",
        params={
            "per_page": per_page, "page": page,
            "sort": "year", "sort_order": "desc",
        },
    )
    return data.get("releases", [])


def artist_releases(artist_id: int, per_page: int = 30, page: int = 1) -> list[dict]:
    data = _get(
        f"https://api.discogs.com/artists/{artist_id}/releases",
        params={
            "per_page": per_page, "page": page,
            "sort": "year", "sort_order": "desc",
        },
    )
    return data.get("releases", [])


def style_recommendations(
    release: Release, limit: int = 15
) -> list[tuple[str, str, int | None]]:
    """Approximation of Discogs' on-site Recommendations: search for releases
    sharing the seed's most-specific styles, sorted by collector demand."""
    if not release.styles:
        return []

    # Discogs treats repeated style= as OR (not AND), and sort=want surfaces
    # iconic-but-tangential releases. Strategy: use one specific style at a
    # time, intersect-by-popularity across the styles, and skip results with
    # implausibly high popularity (those are genre-defining classics, not
    # adjacent finds).
    style_results: list[list[dict]] = []
    for style in release.styles[:3]:
        try:
            data = _get(
                "https://api.discogs.com/database/search",
                params={
                    "type": "release",
                    "style": style,
                    # `want` surfaces all-time classics the user has heard;
                    # `year desc` biases toward fresh releases in the style,
                    # which is what digging actually needs.
                    "sort": "year",
                    "sort_order": "desc",
                    "per_page": 50,
                },
            )
        except Exception:
            continue
        style_results.append(data.get("results") or [])

    # If a release appears in multiple style searches, it's a genuine match
    # for the seed's overall vibe. Score by how many style searches it
    # appeared in, then by aggregate position (lower = more popular).
    scored: dict[int, dict] = {}
    for ranked in style_results:
        for i, r in enumerate(ranked):
            rid = r.get("id")
            if not rid:
                continue
            entry = scored.setdefault(rid, {"r": r, "hits": 0, "score": 0})
            entry["hits"] += 1
            entry["score"] += i
    # Multi-style hits first; within those, lower aggregate score (= higher
    # average rank) wins.
    results = [
        e["r"]
        for e in sorted(
            scored.values(),
            key=lambda e: (-e["hits"], e["score"]),
        )
    ]

    # No year filter: combined with `sort=year desc` above, results are
    # already biased toward fresh releases — which is the digger's goal
    # ("what's new in this style") regardless of the seed's own era.

    out: list[tuple[str, str, int | None]] = []
    seen_artists: set[str] = set()
    for r in results:
        if r.get("id") == release.release_id:
            continue
        title_str = (r.get("title") or "").strip()
        if " - " not in title_str:
            continue
        artist, _, title = title_str.partition(" - ")
        artist = artist.strip()
        title = title.strip()
        if not artist or not title:
            continue
        key = artist.lower()
        if key in seen_artists:
            continue
        seen_artists.add(key)
        out.append((artist, title, release_id_from_stub(r)))
        if len(out) >= limit:
            break
    return out
