"""Local HTTP server: the playlist-run archive plus the interactive quick
search. Threaded so a long discovery run doesn't block page loads.

Routing:
  /               → index: search box + list of playlists
  /search         → quick-search page (auto-runs when ?q= is present)
  /api/discover   → Server-Sent Events stream running one discovery
  /<slug>/        → newest run for that playlist
  /<slug>/<f>     → a specific run
"""

import http.server
import json
import shutil
import subprocess
import threading
import time
import urllib.parse
import webbrowser

from . import config, quick, render

# One discovery at a time: the API modules keep module-level throttle state,
# and interleaved runs would fight over rate limits anyway. A second request
# queues (with a status message) until the first finishes.
_RUN_LOCK = threading.Lock()


def _tailscale_url(port: int) -> str | None:
    """Return a Tailscale URL for the current machine, or None if tailscale
    isn't installed/running. Prefers the MagicDNS hostname over the raw IP
    so the URL survives network changes."""
    if not shutil.which("tailscale"):
        return None
    try:
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


def serve(port: int = 8765, open_browser: bool = True, bind: str = "0.0.0.0") -> int:
    """Default bind is all interfaces so the page stays reachable from phones
    over Tailscale/LAN (see README). Pass bind="127.0.0.1" for local-only."""
    out_dir = config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(out_dir), **kw)

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            if path in ("/", "/index.html"):
                return self._send_index()
            if path == "/search":
                return self._send_search(parsed.query)
            if path == "/api/discover":
                return self._api_discover(parsed.query)
            # /<folder> or /<folder>/ → serve newest *.html in that folder
            parts = [p for p in path.strip("/").split("/") if p]
            if len(parts) == 1:
                sub = out_dir / parts[0]
                if sub.is_dir():
                    if not parsed.path.endswith("/"):
                        self.send_response(301)
                        self.send_header("Location", parsed.path + "/")
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

        def _send_html(self, html_text: str) -> None:
            data = html_text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_index(self) -> None:
            folders = sorted(
                (p.name for p in out_dir.iterdir()
                 if p.is_dir() and any(p.glob("*.html"))),
                key=str.lower,
            )
            self._send_html(
                render.render_template("index.html.j2", folders=folders)
            )

        def _send_search(self, query: str) -> None:
            qs = urllib.parse.parse_qs(query)
            q = (qs.get("q") or [""])[0]
            self._send_html(render.render_template("search.html.j2", q=q))

        def _api_discover(self, query: str) -> None:
            qs = urllib.parse.parse_qs(query)
            q = (qs.get("q") or [""])[0].strip()

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(obj: dict) -> None:
                self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
                self.wfile.flush()

            try:
                if not q:
                    emit({"type": "error", "message": "Empty query."})
                    return
                missing = [
                    k for k in ("DISCOGS_TOKEN", "LASTFM_API_KEY")
                    if not getattr(config, k).strip()
                ]
                if missing:
                    emit({"type": "error",
                          "message": f"Missing API keys in .env: {', '.join(missing)}"})
                    return

                if not _RUN_LOCK.acquire(blocking=False):
                    emit({"type": "status",
                          "message": "Another search is running · queued…"})
                    _RUN_LOCK.acquire()
                try:
                    t0 = time.time()
                    emit({"type": "status", "message": "Resolving input…"})
                    result = quick.run(
                        q,
                        progress=lambda m: emit({"type": "status", "message": m}),
                        on_seed=lambda tr, note: emit({
                            "type": "seed",
                            "artist": tr.artist,
                            "title": tr.title,
                            "note": note,
                        }),
                    )
                    html_frag = render.render_seed_fragment(
                        result.track, result.release, result.candidates
                    )
                    permalink = None
                    if result.out_path:
                        rel = result.out_path.relative_to(config.OUTPUT_DIR)
                        permalink = "/" + "/".join(
                            urllib.parse.quote(p) for p in rel.parts
                        )
                    emit({
                        "type": "result",
                        "html": html_frag,
                        "permalink": permalink,
                        "elapsed": round(time.time() - t0, 1),
                        "candidates": len(result.candidates),
                    })
                finally:
                    _RUN_LOCK.release()
            except quick.InputError as e:
                try:
                    emit({"type": "error", "message": str(e)})
                except OSError:
                    pass
            except (BrokenPipeError, ConnectionResetError):
                pass  # client closed the tab — the run aborts on next emit
            except Exception as e:
                try:
                    emit({"type": "error", "message": f"Search failed: {e}"})
                except OSError:
                    pass

        def log_message(self, fmt, *args):  # quiet the default logger
            pass

    with http.server.ThreadingHTTPServer((bind, port), Handler) as httpd:
        url = f"http://localhost:{port}/"
        print(f"Serving {out_dir} at {url}")
        ts_url = _tailscale_url(port) if bind == "0.0.0.0" else None
        if ts_url:
            print(f"Tailscale: {ts_url}")
        print("Quick search: paste a track name or Spotify/YouTube link at /search")
        print("Press Ctrl-C to stop.")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0
