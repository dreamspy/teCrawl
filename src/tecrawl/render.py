import re
import urllib.parse
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, discover, spotify

# Display order within each seed block — most-specific angles first.
_SOURCE_ORDER = [
    "discogs_artist",
    "discogs_label",
    "discogs_label_mate",
    "discogs_style",
    "discogs_recommendation",
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
    autoescape=select_autoescape(["html", "html.j2"]),
    # No template cache, deliberately. Jinja caches a compiled *module* on each
    # Template object, and `{% from "_seed_block.html.j2" import seed_block %}`
    # hands back that cached module. Since _seed_block.html.j2 itself rarely
    # changes, its module kept closing over a stale copy of the cand_row macro:
    # editing _cand_row.html.j2 alone had no effect on a running server, while
    # _style.css (an {% include %}, re-read every render) updated normally. That
    # produced pages with new styling wrapped around old markup — a genuinely
    # confusing failure mode, since the page looked broken rather than stale.
    # Recompiling costs ~10 ms per seed block against runs that spend ~30 s per
    # seed on API calls, so it is not worth the trap.
    cache_size=0,
)


def youtube_search_url(artist: str, title: str) -> str:
    q = urllib.parse.quote(f"{artist} {title}")
    return f"https://www.youtube.com/results?search_query={q}"


def spotify_search_url(artist: str, title: str) -> str:
    q = urllib.parse.quote(f"{artist} {title}")
    return f"https://open.spotify.com/search/{q}"


def spotify_track_url(spotify_id: str) -> str:
    """Seeds are always tracks and carry only a bare id (spotify.Track has no
    spotify_url the way Candidate does), so the seed block builds the URL from
    the id. Here rather than inline in the template so it's built once."""
    return f"https://open.spotify.com/track/{spotify_id}"


def lastfm_url(artist: str, title: str) -> str:
    """Direct Last.fm track page if we know the title, otherwise the artist
    page. Last.fm's URL scheme uses the artist+track names directly."""
    a = urllib.parse.quote(artist.replace(" ", "+"))
    t = urllib.parse.quote(title.replace(" ", "+"))
    return f"https://www.last.fm/music/{a}/_/{t}"


def discogs_search_url(artist: str, title: str) -> str:
    q = urllib.parse.quote(f"{artist} {title}")
    return f"https://www.discogs.com/search/?q={q}&type=release"


def dig_url(artist: str, title: str) -> str:
    """A plain internal link (not a JS handler) so the browser's own
    click/cmd-click/middle-click behavior just works: normal click digs in
    this tab, cmd/ctrl-click opens the new dig in a new tab. /search?q=...
    auto-starts the search on load (see search.html.j2)."""
    q = urllib.parse.quote(f"{artist} - {title}")
    return f"/search?q={q}"


# Registered as globals so the shared _seed_block macro (and every page
# template) can use them without each render call re-passing the plumbing.
_env.globals.update(
    source_labels=discover.SOURCE_LABELS,
    # Angles a seed run can actually produce, so the seed block can name the
    # ones that came back empty instead of just omitting them. "Same label"
    # is display-order only: discover_for_seed files every label release as
    # either same-artist or label-mate, so it's never an initial section and
    # listing it as "nothing came back" would cry wolf on every page.
    all_sources=[s for s in _SOURCE_ORDER if s != "discogs_label"],
    source_descriptions=discover.SOURCE_DESCRIPTIONS,
    youtube_search_url=youtube_search_url,
    spotify_search_url=spotify_search_url,
    spotify_track_url=spotify_track_url,
    lastfm_url=lastfm_url,
    discogs_search_url=discogs_search_url,
    dig_url=dig_url,
)


def render_template(name: str, **ctx) -> str:
    """Render any template in templates/ to a string (used by the web UI)."""
    return _env.get_template(name).render(**ctx)


def render_top_picks_fragment(top_picks: list[discover.TopPick]) -> str:
    """The bare ★ Top picks block (folder search prepends it when done).
    Empty string when there are no picks."""
    return render_template("_top_picks.html.j2", top_picks=top_picks).strip()


def render_more_rows(
    seed_artist: str, seed_title: str, candidates: list[discover.Candidate]
) -> str:
    """The extra candidate rows the "Show more" button appends into a section.
    Rendered through the same macro as the initial rows so they're identical.
    Only seed.artist/.title are needed here (dig links + feedback context)."""
    if not candidates:
        return ""
    seed = SimpleNamespace(artist=seed_artist, title=seed_title)
    return render_template("_more_rows.html.j2", seed=seed, cands=candidates).strip()


def render_seed_fragment(
    seed: spotify.Track,
    release,
    candidates: list[discover.Candidate],
) -> str:
    """The bare one-seed results block the quick-search page injects."""
    return render_template(
        "_fragment.html.j2",
        seed=seed,
        release=release,
        groups=_group_by_source(candidates),
    )


def render(
    seed_blocks: list[
        tuple[spotify.Track, "discover.discogs.Release | None", list[discover.Candidate]]
    ],
    top_picks: list[discover.TopPick] | None = None,
    playlist_name: str = "",
    folder: str | None = None,
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
    )
    # Group outputs by playlist: output/<playlist name>/<timestamp>.html.
    # The folder name IS the display name (preserving case and spaces), minus
    # filesystem-unsafe characters. Reruns of the same playlist land in the
    # same folder; different playlists get different folders. Quick searches
    # pass an explicit folder so they all pool in one place.
    folder = _folder_name(folder or playlist_name) or "Unlabeled"
    subdir = config.OUTPUT_DIR / folder
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
    path.write_text(html, encoding="utf-8")
    return path


def _folder_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:100]
