import argparse
import sys
from pathlib import Path

from . import config, discover, render, seeds, spotify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tecrawl",
        description=(
            "Generate techno recommendations from a CSV of seed tracks "
            "(export your Spotify playlist with https://watsonbox.github.io/exportify/)."
        ),
    )
    parser.add_argument(
        "source",
        help="Path to a CSV file (Exportify format)",
    )
    parser.add_argument(
        "--max-seeds",
        type=int,
        default=None,
        help="Limit number of seed tracks (useful for testing)",
    )
    args = parser.parse_args(argv)

    missing = config.missing_keys()
    if missing:
        print(f"Missing API keys in .env: {', '.join(missing)}", file=sys.stderr)
        print("See .env.example for setup.", file=sys.stderr)
        return 1

    if spotify.looks_like_playlist_url(args.source):
        print(
            "Spotify playlist URLs can't be read directly: Spotify's Web API\n"
            "blocks /playlists/{id}/tracks for new (Developer Mode) apps.\n\n"
            "Export the playlist to CSV first:\n"
            "  1. Go to https://watsonbox.github.io/exportify/\n"
            "  2. Authorize with Spotify, pick the playlist, click Export\n"
            "  3. Pass the downloaded CSV path to tecrawl",
            file=sys.stderr,
        )
        return 2

    path = Path(args.source)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    print(f"Reading: {path}")
    source_name, seed_list = seeds.from_csv(path)
    if args.max_seeds:
        seed_list = seed_list[: args.max_seeds]
    print(f"Source: {source_name!r} ({len(seed_list)} seed tracks)")

    seed_blocks: list[tuple[spotify.Track, list[discover.Candidate]]] = []
    for i, seed in enumerate(seed_list, 1):
        print(f"[{i}/{len(seed_list)}] {seed.artist} — {seed.title}")
        try:
            candidates = discover.discover_for_seed(seed)
            candidates = discover.resolve_to_spotify(candidates)
        except Exception as e:
            print(f"    ! seed failed: {e}")
            candidates = []
        resolved = sum(1 for c in candidates if c.spotify_id)
        print(f"    → {len(candidates)} candidates, {resolved} resolved on Spotify")
        seed_blocks.append((seed, candidates))

    min_hits = 3
    top_picks = discover.aggregate_top_picks(seed_blocks, min_hits=min_hits)
    print(f"\nTop picks (appear across {min_hits}+ seeds): {len(top_picks)}")
    out_path = render.render(
        seed_blocks, top_picks=top_picks, playlist_name=source_name
    )
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
