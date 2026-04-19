import argparse
import http.server
import socketserver
import sys
import webbrowser
from pathlib import Path

from . import config, discover, render, seeds, spotify


def _serve(port: int = 8765) -> int:
    """Serve the output directory over localhost so YouTube embeds work
    (YouTube's iframe rejects file:// origins; loading via http://localhost
    is the simplest fix). The root URL `/` always serves the most recent
    HTML — that way the user can bookmark http://localhost:8765/ and just
    refresh after each tecrawl run."""
    out_dir = config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    if not list(out_dir.glob("*.html")):
        print(f"No HTML files in {out_dir}. Run `tecrawl <csv>` first.",
              file=sys.stderr)
        return 1

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(out_dir), **kw)

        def do_GET(self):
            # Re-resolve the latest HTML on every request so a freshly-written
            # file shows up on the next refresh without restarting the server.
            if self.path in ("/", "/index.html"):
                htmls = sorted(out_dir.glob("*.html"))
                if htmls:
                    self.path = "/" + htmls[-1].name
            return super().do_GET()

        def log_message(self, fmt, *args):  # quiet the default logger
            pass

    # Allow rebinding immediately after a previous server exit so the user
    # doesn't have to wait out the TCP TIME_WAIT window.
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        url = f"http://localhost:{port}/"
        print(f"Serving {out_dir} at {url}")
        print("Refresh the page to see the latest run after each `tecrawl`.")
        print("Press Ctrl-C to stop.")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0


def main(argv: list[str] | None = None) -> int:
    # Special-case `tecrawl serve` so we don't have to wedge it into the CSV
    # positional. Everything else stays the existing single-arg interface.
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "serve":
        port = 8765
        if len(argv) > 1:
            try:
                port = int(argv[1])
            except ValueError:
                print(f"Invalid port: {argv[1]}", file=sys.stderr)
                return 1
        return _serve(port)

    parser = argparse.ArgumentParser(
        prog="tecrawl",
        description=(
            "Generate techno recommendations from a CSV of seed tracks "
            "(export your Spotify playlist with https://watsonbox.github.io/exportify/). "
            "Run `tecrawl serve` to open the latest output via a local HTTP server "
            "(required for YouTube embeds — they reject file:// origins)."
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

    seed_blocks: list[
        tuple[spotify.Track, "discover.discogs.Release | None", list[discover.Candidate]]
    ] = []
    for i, seed in enumerate(seed_list, 1):
        print(f"[{i}/{len(seed_list)}] {seed.artist} — {seed.title}")
        release = None
        try:
            release, candidates = discover.discover_for_seed(seed)
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

    min_hits = 3
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
