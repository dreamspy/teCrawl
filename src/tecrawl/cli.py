import argparse
import sys
from pathlib import Path

from . import config, discover, localfiles, quick, render, seeds, spotify, web


def _missing_required_keys() -> list[str]:
    """Discogs + Last.fm are the discovery backbone. Spotify is optional at
    runtime (dead credentials degrade to no-auth fallbacks + search links),
    so it's warned about, not required."""
    return [
        k for k in ("DISCOGS_TOKEN", "LASTFM_API_KEY")
        if not getattr(config, k).strip()
    ]


def _run_quick(argv: list[str]) -> int:
    query = " ".join(argv).strip()
    if not query:
        print(
            "Usage: tecrawl quick <track name | spotify link | youtube link>",
            file=sys.stderr,
        )
        return 1
    missing = _missing_required_keys()
    if missing:
        print(f"Missing API keys in .env: {', '.join(missing)}", file=sys.stderr)
        print("See .env.example for setup.", file=sys.stderr)
        return 1
    try:
        result = quick.run(query, progress=print)
    except quick.InputError as e:
        print(e, file=sys.stderr)
        return 2
    resolved_sp = sum(1 for c in result.candidates if c.spotify_id)
    resolved_yt = sum(1 for c in result.candidates if c.youtube_id)
    print(
        f"\n{len(result.candidates)} candidates · "
        f"{resolved_sp} on Spotify · {resolved_yt} on YouTube"
    )
    print(f"Wrote: {result.out_path}")
    print("View it (with working embeds): tecrawl serve")
    return 0


def _run_serve(argv: list[str]) -> int:
    rest = list(argv)
    open_browser = True
    if "--no-open" in rest:
        open_browser = False
        rest.remove("--no-open")
    bind = "0.0.0.0"  # reachable from phone over Tailscale/LAN (see README)
    if "--local" in rest:
        bind = "127.0.0.1"
        rest.remove("--local")
    port = 8765
    if rest:
        try:
            port = int(rest[0])
        except ValueError:
            print(f"Invalid port: {rest[0]}", file=sys.stderr)
            return 1
    return web.serve(port, open_browser=open_browser, bind=bind)


def main(argv: list[str] | None = None) -> int:
    # Subcommands stay hand-dispatched so the plain CSV positional keeps
    # working: `tecrawl <csv>` / `tecrawl serve [port]` / `tecrawl quick <q>`.
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "serve":
        return _run_serve(argv[1:])
    if argv and argv[0] == "quick":
        return _run_quick(argv[1:])

    parser = argparse.ArgumentParser(
        prog="tecrawl",
        description=(
            "Generate techno recommendations from seed tracks: a CSV playlist "
            "export (https://watsonbox.github.io/exportify/) or a folder of "
            "audio files (MP3/M4A/FLAC/… — tags first, filename parsing as "
            "fallback). Other commands: `tecrawl quick <track|link>` for a "
            "one-off single-seed search, `tecrawl serve` for the local web UI "
            "(required for YouTube embeds — they reject file:// origins)."
        ),
    )
    parser.add_argument(
        "source",
        help="Path to a CSV file (Exportify format) or a folder of audio files",
    )
    parser.add_argument(
        "--max-seeds",
        type=int,
        default=None,
        help="Limit number of seed tracks (useful for testing)",
    )
    args = parser.parse_args(argv)

    missing = _missing_required_keys()
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

    path = Path(args.source).expanduser()
    if not path.exists():
        print(f"Not found: {path}", file=sys.stderr)
        return 1

    if path.is_dir():
        print(f"Scanning folder: {path}")
        folder_scan = localfiles.scan(path)
        for p, reason in folder_scan.skipped:
            print(f"  ! skipped {p.name}: {reason}")
        source_name = folder_scan.name
        seed_list = [t.track for t in folder_scan.tracks]
        n_tags = sum(1 for t in folder_scan.tracks if t.how == "tags")
        n_fn = len(folder_scan.tracks) - n_tags
        if not seed_list:
            print(
                "No usable audio files in that folder (need artist/title "
                "tags, or 'Artist - Title' filenames).",
                file=sys.stderr,
            )
            return 1
        print(
            f"Source: folder {source_name!r} ({len(seed_list)} seed tracks: "
            f"{n_tags} from tags, {n_fn} from filenames)"
        )
    elif path.suffix.lower() in localfiles.AUDIO_EXTS:
        got = localfiles.seed_from_file(path)
        if not got:
            print(
                f"Couldn't read an artist/title from {path.name} "
                "(no tags, filename isn't 'Artist - Title').",
                file=sys.stderr,
            )
            return 1
        artist, title, how = got
        print(f"Single audio file → quick search: {artist} — {title} (from {how})")
        return _run_quick([f"{artist} - {title}"])
    else:
        print(f"Reading: {path}")
        source_name, seed_list = seeds.from_csv(path)
        print(f"Source: {source_name!r} ({len(seed_list)} seed tracks)")
    if args.max_seeds and len(seed_list) > args.max_seeds:
        seed_list = seed_list[: args.max_seeds]
        print(f"Limited to first {len(seed_list)} seeds (--max-seeds)")

    seed_blocks: list[
        tuple[spotify.Track, "discover.discogs.Release | None", list[discover.Candidate]]
    ] = []
    run_seen: set[tuple[str, str]] = set()  # rotates "Same vibe" across seeds
    for i, seed in enumerate(seed_list, 1):
        print(f"[{i}/{len(seed_list)}] {seed.artist} — {seed.title}")
        release = None
        try:
            release, candidates = discover.discover_for_seed(seed, run_seen=run_seen)
            candidates = discover.resolve_to_spotify(candidates)
            candidates = discover.resolve_to_youtube(candidates)
        except Exception as e:
            print(f"    ! seed failed: {e}")
            candidates = []
        resolved_sp = sum(1 for c in candidates if c.spotify_id)
        resolved_yt = sum(1 for c in candidates if c.youtube_id)
        print(f"    → {len(candidates)} candidates, "
              f"{resolved_sp} on Spotify, {resolved_yt} on YouTube")
        seed_blocks.append((seed, release, candidates))

    min_hits = discover.default_min_hits(len(seed_list))
    top_picks = discover.aggregate_top_picks(seed_blocks, min_hits=min_hits)
    print(f"\nTop picks (appear across {min_hits}+ seeds): {len(top_picks)}")
    out_path = render.render(
        seed_blocks, top_picks=top_picks, playlist_name=source_name
    )
    print(f"Wrote: {out_path}")
    print("\nTo view (with working YouTube embeds): tecrawl serve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
