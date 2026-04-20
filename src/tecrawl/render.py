import re
import urllib.parse
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, discover, spotify

# Display order within each seed block — most-specific angles first.
_SOURCE_ORDER = [
    "discogs_artist",
    "discogs_label",
    "discogs_label_mate",
    "discogs_style",
    "lastfm_track",
    "lastfm_artist",
]


def _group_by_source(
    candidates: list[discover.Candidate],
) -> list[tuple[str, list[discover.Candidate]]]:
    by_source: dict[str, list[discover.Candidate]] = {}
    for c in candidates:
        by_source.setdefault(c.source, []).append(c)
    ordered = [(s, by_source[s]) for s in _SOURCE_ORDER if s in by_source]
    # Append any unknown sources at the end so nothing silently disappears.
    for s, cs in by_source.items():
        if s not in _SOURCE_ORDER:
            ordered.append((s, cs))
    return ordered

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def youtube_search_url(artist: str, title: str) -> str:
    q = urllib.parse.quote(f"{artist} {title}")
    return f"https://www.youtube.com/results?search_query={q}"


def spotify_search_url(artist: str, title: str) -> str:
    q = urllib.parse.quote(f"{artist} {title}")
    return f"https://open.spotify.com/search/{q}"


def lastfm_url(artist: str, title: str) -> str:
    """Direct Last.fm track page if we know the title, otherwise the artist
    page. Last.fm's URL scheme uses the artist+track names directly."""
    a = urllib.parse.quote(artist.replace(" ", "+"))
    t = urllib.parse.quote(title.replace(" ", "+"))
    return f"https://www.last.fm/music/{a}/_/{t}"


def discogs_search_url(artist: str, title: str) -> str:
    q = urllib.parse.quote(f"{artist} {title}")
    return f"https://www.discogs.com/search/?q={q}&type=release"


def render(
    seed_blocks: list[
        tuple[spotify.Track, "discover.discogs.Release | None", list[discover.Candidate]]
    ],
    top_picks: list[discover.TopPick] | None = None,
    playlist_name: str = "",
) -> Path:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    template = _env.get_template("recommendations.html.j2")
    grouped = [
        (seed, release, _group_by_source(cands))
        for seed, release, cands in seed_blocks
    ]
    html = template.render(
        seeds=grouped,
        top_picks=top_picks or [],
        playlist_name=playlist_name,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        source_labels=discover.SOURCE_LABELS,
        source_descriptions=discover.SOURCE_DESCRIPTIONS,
        youtube_search_url=youtube_search_url,
        spotify_search_url=spotify_search_url,
        lastfm_url=lastfm_url,
        discogs_search_url=discogs_search_url,
    )
    # Group outputs by playlist: output/<playlist name>/<timestamp>.html.
    # The folder name IS the display name (preserving case and spaces), minus
    # filesystem-unsafe characters. Reruns of the same playlist land in the
    # same folder; different playlists get different folders.
    folder = _folder_name(playlist_name) or "Unlabeled"
    subdir = config.OUTPUT_DIR / folder
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
    path.write_text(html, encoding="utf-8")
    return path


def _folder_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:100]
