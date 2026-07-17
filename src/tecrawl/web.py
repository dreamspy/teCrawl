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
from pathlib import Path

from . import config, discover, localfiles, quick, render

# One discovery at a time: the API modules keep module-level throttle state,
# and interleaved runs would fight over rate limits anyway. A second request
# queues (with a status message) until the first finishes.
_RUN_LOCK = threading.Lock()


def _local_path(q: str) -> Path | None:
    """A pasted absolute path (or file:// URL from a Finder drag) → Path.
    None when the query doesn't even look like a path. The caller decides
    what to do with dirs vs files vs nonexistent paths.

    Deliberate trade-off: the server may be reachable over LAN/Tailscale
    (see serve()), so anyone on those trusted networks can point discovery
    at any local folder. It only ever reads audio tags — acceptable for a
    personal tool on networks you control."""
    s = q.strip()
    if s.startswith("file://"):
        s = urllib.parse.unquote(urllib.parse.urlsplit(s).path)
    if not s.startswith(("/", "~", "./", "../")):
        return None
    try:
        return Path(s).expanduser()
    except RuntimeError:  # "~nosuchuser" — expanduser can raise
        return Path(s)


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


def _pick_folder() -> tuple[str | None, str | None]:
    """(path, error) from a native macOS folder-picker dialog. The dialog
    opens on the Mac running the server, so this only helps when the browser
    is on that same Mac — phone users get the error string. Cancel returns
    (None, None)."""
    script = (
        'POSIX path of (choose folder with prompt '
        '"Pick a folder of audio files for teCrawl")'
    )
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=180,
        )
    except FileNotFoundError:
        return None, "Folder picker needs macOS (osascript not found)."
    except subprocess.TimeoutExpired:
        return None, "Folder picker timed out."
    if r.returncode != 0:  # Cancel ("User canceled." on stderr)
        return None, None
    path = r.stdout.strip()
    return (path or None), None


def serve(port: int = 8765, open_browser: bool = True, bind: str = "0.0.0.0") -> int:
    """Default bind is all interfaces so the page stays reachable from phones
    over Tailscale/LAN (see README). Pass bind="127.0.0.1" for local-only."""
    out_dir = config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    # Shown in page footers so a stale server (started before a code update,
    # so it lacks the newest features) is visible at a glance.
    server_started = time.strftime("%Y-%m-%d %H:%M")

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
            if path == "/api/pick-folder":
                return self._api_pick_folder()
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
                render.render_template(
                    "index.html.j2",
                    folders=folders,
                    server_started=server_started,
                )
            )

        def _send_search(self, query: str) -> None:
            qs = urllib.parse.parse_qs(query)
            q = (qs.get("q") or [""])[0]
            self._send_html(render.render_template(
                "search.html.j2", q=q, server_started=server_started,
            ))

        def _api_pick_folder(self) -> None:
            path, err = _pick_folder()
            body: dict = {}
            if path:
                body["path"] = path
            elif err:
                body["error"] = err
            data = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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

                folder = None
                p = _local_path(q)
                if p is not None:
                    if p.is_dir():
                        folder = p
                    elif (
                        p.is_file()
                        and p.suffix.lower() in localfiles.AUDIO_EXTS
                    ):
                        # A single audio file is just a quick search whose
                        # artist/title come from its tags (or filename).
                        got = localfiles.seed_from_file(p)
                        if not got:
                            emit({"type": "error", "message": (
                                f"Couldn't read an artist/title from {p.name} "
                                "(no tags, filename isn't 'Artist - Title')."
                            )})
                            return
                        artist, title, how = got
                        emit({"type": "note", "message":
                              f"{p.name} → {artist} — {title} (from {how})"})
                        q = f"{artist} - {title}"
                    elif not p.exists():
                        emit({"type": "error",
                              "message": f"Path not found: {p}"})
                        return
                    else:
                        emit({"type": "error", "message": (
                            f"{p.name} isn't a folder or a supported "
                            "audio file."
                        )})
                        return

                if not _RUN_LOCK.acquire(blocking=False):
                    emit({"type": "status",
                          "message": "Another search is running · queued…"})
                    _RUN_LOCK.acquire()
                try:
                    if folder is not None:
                        self._run_folder(folder, emit)
                        return
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

        def _run_folder(self, folder, emit) -> None:
            """Multi-seed run over a local folder: each seed's results block
            streams to the page the moment that seed finishes. If the tab
            closes mid-run, the current seed completes, everything done so
            far still persists to output/ (the API work is already spent),
            and the run stops instead of grinding through the rest."""
            dead = [False]

            def say_json(obj: dict) -> None:
                if dead[0]:
                    return
                try:
                    emit(obj)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    dead[0] = True

            def status(m: str) -> None:
                say_json({"type": "status", "message": m})

            t0 = time.time()
            fscan = localfiles.scan(folder)
            for p, reason in fscan.skipped:
                say_json({"type": "note",
                          "message": f"skipped {p.name} — {reason}"})
            if not fscan.tracks:
                say_json({"type": "error", "message": (
                    "No usable audio files in that folder (need artist/title "
                    "tags, or 'Artist - Title' filenames)."
                )})
                return
            n = len(fscan.tracks)
            n_tags = sum(1 for t in fscan.tracks if t.how == "tags")
            say_json({"type": "note", "message": (
                f"Folder “{fscan.name}”: {n} seed track{'s' if n != 1 else ''} "
                f"({n_tags} from tags, {n - n_tags} from filenames)"
            )})

            seed_blocks = []
            total_cands = 0
            for i, st in enumerate(fscan.tracks, 1):
                if dead[0]:
                    break
                prefix = f"[{i}/{n}] {st.track.artist} — {st.track.title}"
                status(f"{prefix} · digging…")
                say = lambda m, _p=prefix: status(f"{_p} · {m}")
                release = None
                try:
                    release, cands = discover.discover_for_seed(
                        st.track, progress=say
                    )
                    cands = discover.resolve_to_spotify(cands, progress=say)
                    cands = discover.resolve_to_youtube(cands, progress=say)
                except Exception as e:
                    say_json({"type": "note", "message": (
                        f"⚠ seed failed — {st.track.artist} — "
                        f"{st.track.title}: {e}"
                    )})
                    cands = []
                seed_blocks.append((st.track, release, cands))
                total_cands += len(cands)
                say_json({
                    "type": "seed_html",
                    "html": render.render_seed_fragment(st.track, release, cands),
                })

            top_picks = discover.aggregate_top_picks(
                seed_blocks, min_hits=discover.default_min_hits(len(seed_blocks))
            )
            out_path = render.render(
                seed_blocks, top_picks=top_picks, playlist_name=fscan.name
            )
            rel = out_path.relative_to(config.OUTPUT_DIR)
            say_json({
                "type": "done",
                "tops_html": render.render_top_picks_fragment(top_picks),
                "permalink": "/" + "/".join(
                    urllib.parse.quote(part) for part in rel.parts
                ),
                "elapsed": round(time.time() - t0, 1),
                "seeds": len(seed_blocks),
                "candidates": total_cands,
            })

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
