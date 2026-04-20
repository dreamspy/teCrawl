import argparse
import http.server
import shutil
import socketserver
import subprocess
import sys
import urllib.parse
import webbrowser
from pathlib import Path

from . import config, discover, render, seeds, spotify


_INDEX_CSS = """
:root { --bg:#0a0a0a; --card:#161616; --text:#e8e8e8; --muted:#888;
        --accent:#1db954; --border:#2a2a2a; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
       padding:2rem 1rem; line-height:1.4; }
.wrap { max-width:720px; margin:0 auto; }
h1 { margin:0 0 .25rem; font-size:1.4rem; }
.meta { color:var(--muted); font-size:.85rem; margin-bottom:2rem; }
a { color:var(--text); text-decoration:none; }
a:hover { color:var(--accent); }
.row { display:flex; align-items:center; justify-content:space-between;
       padding:1rem 1.25rem; background:var(--card);
       border:1px solid var(--border); border-radius:8px; margin-bottom:.6rem; }
.row .name { font-weight:600; }
.row .count { color:var(--muted); font-size:.85rem; }
.empty { color:var(--muted); padding:1rem 0; }
.section-title { color:var(--muted); font-size:.8rem; letter-spacing:.08em;
                 text-transform:uppercase; margin:2rem 0 .5rem; }
"""


def _render_index_html(folders: list[Path]) -> str:
    import html
    if not folders:
        body = ('<div class="empty">No playlists yet. '
                'Run <code>tecrawl &lt;csv&gt;</code> first.</div>')
    else:
        body = "\n".join(
            f'<a class="row" href="/{urllib.parse.quote(d.name)}/">'
            f'<span class="name">{html.escape(d.name)}</span></a>'
            for d in folders
        )
    return (
        "<!DOCTYPE html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>teCrawl — playlists</title>"
        f"<style>{_INDEX_CSS}</style></head><body><div class=wrap>"
        "<h1>teCrawl</h1>"
        f"{body}"
        "</div></body></html>"
    )


def _tailscale_url(port: int) -> str | None:
    """Return a Tailscale URL for the current machine, or None if tailscale
    isn't installed/running. Prefers the MagicDNS hostname over the raw IP
    so the URL survives network changes."""
    if not shutil.which("tailscale"):
        return None
    try:
        import json
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=2, check=True,
        ).stdout
        self_node = json.loads(out).get("Self") or {}
        dns = (self_node.get("DNSName") or "").rstrip(".")
        if dns:
            return f"http://{dns}:{port}/"
        ips = self_node.get("TailscaleIPs") or []
        if ips:
            return f"http://{ips[0]}:{port}/"
    except (subprocess.SubprocessError, ValueError, OSError):
        return None
    return None


def _serve(port: int = 8765) -> int:
    """Serve the output directory over HTTP so YouTube embeds work
    (YouTube's iframe rejects file:// origins). Outputs are grouped by
    playlist under output/<slug>/<timestamp>.html. Routing:
      /            → index page listing playlists (newest run per playlist)
      /<slug>/     → newest run for that playlist
      /<slug>/<f>  → specific run
    Re-resolved on every request so a fresh run shows up on the next
    refresh without restarting the server."""
    out_dir = config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(out_dir), **kw)

        def do_GET(self):
            path = urllib.parse.unquote(self.path.split("?", 1)[0])
            if path in ("/", "/index.html"):
                return self._send_index()
            # /<folder> or /<folder>/ → serve newest *.html in that folder
            parts = [p for p in path.strip("/").split("/") if p]
            if len(parts) == 1:
                sub = out_dir / parts[0]
                if sub.is_dir():
                    if not self.path.endswith("/"):
                        self.send_response(301)
                        self.send_header("Location", self.path + "/")
                        self.end_headers()
                        return
                    htmls = sorted(sub.glob("*.html"))
                    if htmls:
                        self.path = (
                            "/"
                            + urllib.parse.quote(parts[0])
                            + "/"
                            + urllib.parse.quote(htmls[-1].name)
                        )
                    else:
                        self.send_error(404, "No runs in this playlist yet")
                        return
            return super().do_GET()

        def _send_index(self):
            folders = sorted(
                (p for p in out_dir.iterdir()
                 if p.is_dir() and any(p.glob("*.html"))),
                key=lambda d: d.name.lower(),
            )
            data = _render_index_html(folders).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):  # quiet the default logger
            pass

    # Allow rebinding immediately after a previous server exit so the user
    # doesn't have to wait out the TCP TIME_WAIT window.
    socketserver.TCPServer.allow_reuse_address = True
    # Bind to 0.0.0.0 so the page is reachable from phones over Tailscale
    # (or LAN). Still opens localhost on the local machine.
    with socketserver.TCPServer(("0.0.0.0", port), Handler) as httpd:
        url = f"http://localhost:{port}/"
        print(f"Serving {out_dir} at {url}")
        ts_url = _tailscale_url(port)
        if ts_url:
            print(f"Tailscale: {ts_url}")
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
