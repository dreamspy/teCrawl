from typing import NamedTuple

from . import discogs, lastfm, spotify, youtube


class Candidate(NamedTuple):
    artist: str
    title: str
    source: str
    source_detail: str
    spotify_id: str | None
    spotify_url: str | None
    spotify_type: str | None  # 'track' or 'album', None if unresolved
    youtube_id: str | None = None


class SeedRef(NamedTuple):
    display: str
    spotify_id: str | None


class TopPick(NamedTuple):
    artist: str
    title: str
    hits: int  # how many distinct seeds surfaced this
    sources: list[str]  # discovery angles that surfaced it (deduped)
    seeds: list[SeedRef]  # seeds that surfaced this candidate
    spotify_id: str | None
    spotify_url: str | None
    spotify_type: str | None
    youtube_id: str | None = None


SOURCE_LABELS = {
    "discogs_label": "Same label",
    "discogs_artist": "Same artist",
    "discogs_label_mate": "Label-mate",
    "discogs_style": "Same vibe",
    "lastfm_track": "Sounds similar",
    "lastfm_artist": "Similar artist",
}

# Plain-language explanation of where each category comes from. Shown as a
# small italic caption next to the section header so it's clear which
# discovery angle produced these candidates and why.
SOURCE_DESCRIPTIONS = {
    "discogs_label": "Discogs · other releases on the same record label",
    "discogs_artist": "Discogs · other releases by this artist",
    "discogs_label_mate": "Discogs · other artists released on the same label",
    "discogs_style": "Discogs · fresh releases sharing the seed's styles",
    "lastfm_track": "Last.fm · scrobble-based similar tracks",
    "lastfm_artist": "Last.fm · top tracks by similar artists",
}

# Discogs candidates are release/album-level; Last.fm candidates are track-level
_ALBUM_SOURCES = {
    "discogs_label",
    "discogs_artist",
    "discogs_label_mate",
    "discogs_style",
}

CANDIDATES_PER_SEED = 20


def _primary_artist(seed_artist: str) -> str:
    # Exportify joins multiple artists with ';', Spotify display strings with ', '
    for sep in (";", ","):
        if sep in seed_artist:
            return seed_artist.split(sep)[0].strip()
    return seed_artist.strip()


def discover_for_seed(seed: spotify.Track) -> list[Candidate]:
    candidates: list[Candidate] = []
    primary = _primary_artist(seed.artist)

    release = None
    try:
        release = discogs.search_release(primary, seed.title)
    except Exception as e:
        print(f"    ! discogs search failed: {e}")

    if release and release.label_id:
        try:
            for r in discogs.label_releases(release.label_id, per_page=30):
                a = (r.get("artist") or "").strip()
                t = (r.get("title") or "").strip()
                if not a or not t:
                    continue
                if a.lower() == primary.lower():
                    src = "discogs_artist"
                    detail = f"on {release.label}"
                else:
                    src = "discogs_label_mate"
                    detail = f"label: {release.label}"
                candidates.append(
                    Candidate(a, t, src, detail, None, None, None)
                )
        except Exception as e:
            print(f"    ! discogs label_releases failed: {e}")

    if release and release.artist_id:
        try:
            for r in discogs.artist_releases(release.artist_id, per_page=15):
                a = (r.get("artist") or "").strip() or primary
                t = (r.get("title") or "").strip()
                if not t:
                    continue
                candidates.append(
                    Candidate(
                        a, t, "discogs_artist", f"by {primary}", None, None, None
                    )
                )
        except Exception as e:
            print(f"    ! discogs artist_releases failed: {e}")

    if release and release.styles:
        try:
            style_label = " / ".join(release.styles[:3])
            for a, t in discogs.style_recommendations(release, limit=15):
                candidates.append(
                    Candidate(
                        a,
                        t,
                        "discogs_style",
                        f"style: {style_label}",
                        None,
                        None,
                        None,
                    )
                )
        except Exception as e:
            print(f"    ! discogs style_recommendations failed: {e}")

    try:
        for a, t in lastfm.similar_tracks(primary, seed.title, limit=10):
            candidates.append(
                Candidate(
                    a,
                    t,
                    "lastfm_track",
                    f"similar to {primary} — {seed.title}",
                    None,
                    None,
                    None,
                )
            )
    except Exception as e:
        print(f"    ! lastfm similar_tracks failed: {e}")

    try:
        for sim_artist in lastfm.similar_artists(primary, limit=4):
            for a, t in lastfm.artist_top_tracks(sim_artist, limit=2):
                candidates.append(
                    Candidate(
                        a,
                        t,
                        "lastfm_artist",
                        f"like {primary}",
                        None,
                        None,
                        None,
                    )
                )
    except Exception as e:
        print(f"    ! lastfm similar_artists failed: {e}")

    seen: set[tuple[str, str]] = set()
    seed_key = (primary.lower(), seed.title.lower())
    unique: list[Candidate] = []
    for c in candidates:
        key = (c.artist.lower(), c.title.lower())
        if key in seen or key == seed_key:
            continue
        seen.add(key)
        unique.append(c)

    return _balance_across_sources(unique, CANDIDATES_PER_SEED)


def aggregate_top_picks(
    seed_blocks: list[tuple[spotify.Track, list[Candidate]]],
    min_hits: int = 2,
) -> list[TopPick]:
    """A candidate that surfaces from multiple seeds is much higher signal
    than a one-off — those are tracks that fit several things you already
    like, not just one. Group by (artist, title) across seeds, keep anything
    that appeared from 2+ seeds, sort by hit count."""
    by_key: dict[tuple[str, str], dict] = {}
    for seed, cands in seed_blocks:
        seed_ref = SeedRef(
            display=f"{_primary_artist(seed.artist)} — {seed.title}",
            spotify_id=seed.spotify_id,
        )
        # A single seed can list the same candidate from multiple angles
        # (e.g. label-mate AND same vibe). Count that as ONE hit for this
        # seed but keep both source tags.
        seen_in_seed: set[tuple[str, str]] = set()
        for c in cands:
            key = (c.artist.lower(), c.title.lower())
            entry = by_key.setdefault(
                key,
                {
                    "artist": c.artist,
                    "title": c.title,
                    "sources": [],
                    "seeds": [],
                    "spotify_id": None,
                    "spotify_url": None,
                    "spotify_type": None,
                    "youtube_id": None,
                },
            )
            if c.source not in entry["sources"]:
                entry["sources"].append(c.source)
            if key not in seen_in_seed:
                entry["seeds"].append(seed_ref)
                seen_in_seed.add(key)
            # Keep the first resolved Spotify hit we see for this candidate.
            if c.spotify_id and not entry["spotify_id"]:
                entry["spotify_id"] = c.spotify_id
                entry["spotify_url"] = c.spotify_url
                entry["spotify_type"] = c.spotify_type
            if c.youtube_id and not entry["youtube_id"]:
                entry["youtube_id"] = c.youtube_id

    picks = [
        TopPick(
            artist=e["artist"],
            title=e["title"],
            hits=len(e["seeds"]),
            sources=e["sources"],
            seeds=e["seeds"],
            spotify_id=e["spotify_id"],
            spotify_url=e["spotify_url"],
            spotify_type=e["spotify_type"],
            youtube_id=e["youtube_id"],
        )
        for e in by_key.values()
        if len(e["seeds"]) >= min_hits
    ]
    # Highest cross-seed hits first; tie-break by source breadth (a candidate
    # found via 3 different angles is stronger than one found via 1 angle 3x).
    picks.sort(key=lambda p: (-p.hits, -len(p.sources), p.artist.lower()))
    return picks


def _balance_across_sources(candidates: list[Candidate], cap: int) -> list[Candidate]:
    """Round-robin pick across sources so no single angle hogs all slots."""
    by_source: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_source.setdefault(c.source, []).append(c)
    out: list[Candidate] = []
    while len(out) < cap and any(by_source.values()):
        for src in list(by_source.keys()):
            if not by_source[src]:
                continue
            out.append(by_source[src].pop(0))
            if len(out) >= cap:
                break
    return out


def resolve_to_youtube(candidates: list[Candidate]) -> list[Candidate]:
    """Look up a YouTube video ID per candidate (no API key — public search
    page scrape, cached). YouTube has nearly everything techno releases on
    Bandcamp/Discogs do, so this is the catch-all playable for candidates
    that aren't on Spotify."""
    out: list[Candidate] = []
    for c in candidates:
        if c.youtube_id:
            out.append(c)
            continue
        try:
            vid = youtube.search_video_id(c.artist, c.title)
        except Exception:
            vid = None
        out.append(c._replace(youtube_id=vid))
    return out


def resolve_to_spotify(candidates: list[Candidate]) -> list[Candidate]:
    out: list[Candidate] = []
    for c in candidates:
        try:
            if c.source in _ALBUM_SOURCES:
                hit = spotify.search_album(c.artist, c.title)
                kind = "album"
            else:
                hit = spotify.search_track(c.artist, c.title)
                kind = "track"
        except Exception:
            hit = None
            kind = None
        if hit and hit.spotify_id:
            out.append(
                c._replace(
                    spotify_id=hit.spotify_id,
                    spotify_url=f"https://open.spotify.com/{kind}/{hit.spotify_id}",
                    spotify_type=kind,
                )
            )
        else:
            out.append(c)
    return out
