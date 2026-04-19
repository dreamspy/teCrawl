import csv
from pathlib import Path

from . import spotify

_TITLE_COLS = ["track name", "title", "name"]
_ARTIST_COLS = ["artist name(s)", "artist name", "artists", "artist"]
_URI_COLS = ["track uri", "spotify uri", "uri"]


def _find_col(cols: list[str], candidates: list[str]) -> str | None:
    lower_to_orig = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand in lower_to_orig:
            return lower_to_orig[cand]
    return None


def from_csv(path: Path) -> tuple[str, list[spotify.Track]]:
    """Parse an Exportify-style CSV. Returns (source_name, seed list)."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames or [])
        title_col = _find_col(cols, _TITLE_COLS)
        artist_col = _find_col(cols, _ARTIST_COLS)
        uri_col = _find_col(cols, _URI_COLS)
        if not title_col or not artist_col:
            raise ValueError(
                f"CSV missing required columns. Found: {cols}. "
                "Need a track-title column (e.g. 'Track Name') and "
                "an artist column (e.g. 'Artist Name(s)')."
            )
        out: list[spotify.Track] = []
        for row in reader:
            title = (row.get(title_col) or "").strip()
            artist = (row.get(artist_col) or "").strip()
            if not title or not artist:
                continue
            uri = (row.get(uri_col) or "").strip() if uri_col else ""
            spotify_id = (
                uri.split(":")[-1] if uri.startswith("spotify:track:") else None
            )
            out.append(
                spotify.Track(artist=artist, title=title, spotify_id=spotify_id)
            )
    return path.stem, out
