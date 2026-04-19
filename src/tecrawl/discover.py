from typing import NamedTuple

from . import discogs, lastfm, spotify


class Candidate(NamedTuple):
    artist: str
    title: str
    source: str
    source_detail: str
    spotify_id: str | None
    spotify_url: str | None
    spotify_type: str | None  # 'track' or 'album', None if unresolved


SOURCE_LABELS = {
    "discogs_label": "Same label",
    "discogs_artist": "Same artist",
    "discogs_label_mate": "Label-mate",
    "lastfm_track": "Sounds similar",
    "lastfm_artist": "Similar artist",
}

# Discogs candidates are release/album-level; Last.fm candidates are track-level
_ALBUM_SOURCES = {"discogs_label", "discogs_artist", "discogs_label_mate"}

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

    return unique[:CANDIDATES_PER_SEED]


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
