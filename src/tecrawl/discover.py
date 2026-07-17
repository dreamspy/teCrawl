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
    discogs_release_id: int | None = None  # set for Discogs-sourced candidates


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
    discogs_release_id: int | None = None


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


def default_min_hits(n_seeds: int) -> int:
    """3+ cross-seed hits is the meaningful bar for top picks, but tiny runs
    (a 2-file folder) can never reach it — degrade so they stay possible."""
    return 3 if n_seeds >= 3 else 2


def _primary_artist(seed_artist: str) -> str:
    # Exportify joins multiple artists with ';', Spotify display strings with ', '
    for sep in (";", ","):
        if sep in seed_artist:
            return seed_artist.split(sep)[0].strip()
    return seed_artist.strip()


def discover_for_seed(
    seed: spotify.Track,
    progress=None,
) -> tuple[discogs.Release | None, list[Candidate]]:
    """`progress` (optional) receives human-readable stage messages — used by
    the quick-search page to stream live status. Warnings still print to the
    console when no progress callback is given (the CLI path)."""
    say = progress or (lambda m: None)

    def warn(msg: str) -> None:
        if progress:
            progress(f"⚠ {msg}")
        else:
            print(f"    ! {msg}")

    candidates: list[Candidate] = []
    primary = _primary_artist(seed.artist)

    release = None
    say("Discogs: looking up the release…")
    try:
        release = discogs.search_release(primary, seed.title)
    except Exception as e:
        warn(f"discogs search failed: {e}")
    if release:
        found_bits = [b for b in [release.label, str(release.year or "")] if b]
        say(f"Discogs: found “{release.title}” ({' · '.join(found_bits) or 'no label info'})")

    if release and release.label_id:
        say(f"Discogs: other releases on {release.label}…")
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
                    Candidate(
                        a, t, src, detail, None, None, None,
                        discogs_release_id=discogs.release_id_from_stub(r),
                    )
                )
        except Exception as e:
            warn(f"discogs label_releases failed: {e}")

    if release and release.artist_id:
        say(f"Discogs: other releases by {release.artist}…")
        try:
            for r in discogs.artist_releases(release.artist_id, per_page=15):
                a = (r.get("artist") or "").strip() or primary
                t = (r.get("title") or "").strip()
                if not t:
                    continue
                candidates.append(
                    Candidate(
                        a, t, "discogs_artist", f"by {primary}",
                        None, None, None,
                        discogs_release_id=discogs.release_id_from_stub(r),
                    )
                )
        except Exception as e:
            warn(f"discogs artist_releases failed: {e}")

    if release and release.styles:
        say(f"Discogs: fresh releases in {', '.join(release.styles[:3])}…")
        try:
            style_label = " / ".join(release.styles[:3])
            for a, t, rid in discogs.style_recommendations(release, limit=15):
                candidates.append(
                    Candidate(
                        a,
                        t,
                        "discogs_style",
                        f"style: {style_label}",
                        None,
                        None,
                        None,
                        discogs_release_id=rid,
                    )
                )
        except Exception as e:
            warn(f"discogs style_recommendations failed: {e}")

    say("Last.fm: scrobble-similar tracks…")
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
        warn(f"lastfm similar_tracks failed: {e}")

    say("Last.fm: similar artists' top tracks…")
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
        warn(f"lastfm similar_artists failed: {e}")

    seen: set[tuple[str, str]] = set()
    seed_key = (primary.lower(), seed.title.lower())
    unique: list[Candidate] = []
    for c in candidates:
        key = (c.artist.lower(), c.title.lower())
        if key in seen or key == seed_key:
            continue
        seen.add(key)
        unique.append(c)

    return release, _balance_across_sources(unique, CANDIDATES_PER_SEED)


def aggregate_top_picks(
    seed_blocks: list[tuple[spotify.Track, "discogs.Release | None", list[Candidate]]],
    min_hits: int = 2,
) -> list[TopPick]:
    """A candidate that surfaces from multiple seeds is much higher signal
    than a one-off — those are tracks that fit several things you already
    like, not just one. Group by (artist, title) across seeds, keep anything
    that appeared from 2+ seeds, sort by hit count."""
    by_key: dict[tuple[str, str], dict] = {}
    for seed, _release, cands in seed_blocks:
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
                    "discogs_release_id": None,
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
            if c.discogs_release_id and not entry["discogs_release_id"]:
                entry["discogs_release_id"] = c.discogs_release_id

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
            discogs_release_id=e["discogs_release_id"],
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


def resolve_to_youtube(
    candidates: list[Candidate], progress=None
) -> list[Candidate]:
    """Look up a YouTube video ID per candidate (no API key — public search
    page scrape, cached). YouTube has nearly everything techno releases on
    Bandcamp/Discogs do, so this is the catch-all playable for candidates
    that aren't on Spotify."""
    say = progress or (lambda m: None)
    out: list[Candidate] = []
    n = len(candidates)
    for i, c in enumerate(candidates, 1):
        if c.youtube_id:
            out.append(c)
            continue
        say(f"YouTube: finding videos… {i}/{n}")
        try:
            vid = youtube.search_video_id(c.artist, c.title)
        except Exception:
            vid = None
        out.append(c._replace(youtube_id=vid))
    return out


def resolve_to_spotify(
    candidates: list[Candidate], progress=None
) -> list[Candidate]:
    say = progress or (lambda m: None)
    out: list[Candidate] = []
    n = len(candidates)
    unavailable = False
    for i, c in enumerate(candidates, 1):
        hit = None
        kind = None
        if not unavailable:
            say(f"Spotify: matching candidates… {i}/{n}")
            try:
                if c.source in _ALBUM_SOURCES:
                    hit = spotify.search_album(c.artist, c.title)
                    kind = "album"
                else:
                    hit = spotify.search_track(c.artist, c.title)
                    kind = "track"
            except spotify.SpotifyUnavailable as e:
                # Don't hammer a dead/rate-limited endpoint for every
                # remaining candidate — they all fall back to search links.
                unavailable = True
                say(f"Spotify skipped ({e})")
            except Exception:
                pass
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
