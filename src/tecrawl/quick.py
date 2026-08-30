"""One-shot discovery from a single pasted input: a track name, a Spotify
track/album link, or a YouTube link. Used by the /search web page and the
`tecrawl quick` command."""

from pathlib import Path
from typing import Callable, NamedTuple

from . import discover, render, seed_input, spotify
from .seed_input import InputError  # re-exported for callers

__all__ = ["run", "QuickResult", "InputError", "QUICK_FOLDER"]

# All quick searches pool into one output folder, so the index page shows a
# single "Quick searches" entry whose newest run is always the last search.
QUICK_FOLDER = "Quick searches"


class QuickResult(NamedTuple):
    track: spotify.Track
    release: object  # discogs.Release | None
    candidates: list[discover.Candidate]
    out_path: Path | None
    note: str


def run(
    query: str,
    progress: Callable[[str], None] | None = None,
    on_seed: Callable[[spotify.Track, str], None] | None = None,
    persist: bool = True,
    cancel=None,
) -> QuickResult:
    """Resolve the input, run the full discovery pipeline on it, and (by
    default) persist a standalone HTML page next to the playlist runs.
    Raises InputError with a user-facing message for un-parseable input.

    `cancel` (optional, `threading.Event`): forwarded to the discovery calls,
    which raise `discover.Cancelled` at their progress checkpoints once it's
    set — the caller is expected to catch that."""
    say = progress or (lambda m: None)

    track, note = seed_input.resolve(query, progress=say, cancel=cancel)
    if on_seed:
        on_seed(track, note)
    say(f"Seed: {track.artist} — {track.title} ({note})")

    release, candidates = discover.discover_for_seed(
        track, progress=say, cancel=cancel
    )
    say(f"{len(candidates)} candidates · matching on Spotify…")
    candidates = discover.resolve_to_spotify(candidates, progress=say, cancel=cancel)
    candidates = discover.resolve_to_youtube(candidates, progress=say, cancel=cancel)

    out_path = None
    if persist:
        out_path = render.render(
            [(track, release, candidates)],
            top_picks=[],
            playlist_name=f"Quick search · {track.artist} — {track.title}",
            folder=QUICK_FOLDER,
        )
    return QuickResult(track, release, candidates, out_path, note)
