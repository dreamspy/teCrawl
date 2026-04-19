import urllib.parse
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, discover, spotify

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


def render(
    seed_blocks: list[tuple[spotify.Track, list[discover.Candidate]]],
    playlist_name: str = "",
) -> Path:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    template = _env.get_template("recommendations.html.j2")
    html = template.render(
        seeds=seed_blocks,
        playlist_name=playlist_name,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        source_labels=discover.SOURCE_LABELS,
        youtube_search_url=youtube_search_url,
        spotify_search_url=spotify_search_url,
    )
    path = config.OUTPUT_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
    path.write_text(html, encoding="utf-8")
    return path
