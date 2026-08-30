"""Local HTTP server: the playlist-run archive plus the interactive quick
search. Threaded so a long discovery run doesn't block page loads.

Routing:
  /               → index: search box + list of playlists
  /search         → quick-search page (auto-runs when ?q= is present)
  /queue          → the "grab this later" download queue
  /api/discover   → Server-Sent Events stream running one discovery
  /api/stop       → cancel the run currently holding _RUN_LOCK
  /<slug>/        → newest run for that playlist
  /<slug>/<f>     → a specific run
"""

import http.server
import json
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from html import escape as html_escape
from pathlib import Path

from . import (
    cache, config, discover, dlqueue, feedback, localfiles, quick, render,
    spotify_connect,
)

# Run files are named <YYYYMMDD>-<HHMMSS>.html (see render.write_page). We parse
# that back into a real timestamp for display and sorting; anything that doesn't
# match (hand-dropped file, older naming) falls back to the file's mtime.
_TS_RE = re.compile(r"^(\d{8})-(\d{6})$")
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _run_timestamp(p: Path) -> datetime:
    m = _TS_RE.match(p.stem)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    return datetime.fromtimestamp(p.stat().st_mtime)


def _run_label(p: Path) -> str:
    """The <title> of a run, minus the "teCrawl — " prefix, so each run in a
    folder shows what it actually dug (the seed track / playlist name). Only
    the head of the file is read since the title is right at the top."""
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(2000)
    except OSError:
        return p.stem
    m = _TITLE_RE.search(head)
    if not m:
        return p.stem
    title = " ".join(m.group(1).split())
    for sep in (" — ", " - "):
        prefix = "teCrawl" + sep
        if title.startswith(prefix):
            return title[len(prefix):]
    return title


def _folder_summary(sub: Path) -> dict:
    """Per-folder info for the index: how many runs and when the newest was."""
    stamps = [_run_timestamp(p) for p in sub.glob("*.html")]
    newest = max(stamps, default=None)
    return {
        "name": sub.name,
        "count": len(stamps),
        "when": newest.strftime("%Y-%m-%d %H:%M") if newest else "",
        "sort": newest or datetime.min,
    }


def _folder_runs(sub: Path) -> list[dict]:
    """Every run in a folder, newest first, labelled by its seed for browsing."""
    runs = [
        {
            "file": p.name,
            "label": _run_label(p),
            "when": _run_timestamp(p).strftime("%Y-%m-%d %H:%M"),
            "sort": _run_timestamp(p),
        }
        for p in sub.glob("*.html")
    ]
    runs.sort(key=lambda r: r["sort"], reverse=True)
    return runs

# One discovery at a time: the API modules keep module-level throttle state,
# and interleaved runs would fight over rate limits anyway. A second request
# queues (with a status message) until the first finishes.
_RUN_LOCK = threading.Lock()

# Cooperative cancel flag for whichever run currently holds _RUN_LOCK. Cleared
# right after a run acquires the lock, set by POST /api/stop, and checked at
# the say()/progress() checkpoints inside discover.py — a plain threading
# thread can't be force-killed mid-network-call, so this is the only safe way
# to cut a run short. One event (not per-run) is enough because _RUN_LOCK
# already serializes runs to at most one at a time.
_CANCEL_EVENT = threading.Event()


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "on", "yes")


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



def _notice_page(title: str, detail: str, ok: bool = False) -> str:
    """Minimal standalone page for the OAuth round trip (the callback tab has
    no run page to attach a message to)."""
    colour = "#1db954" if ok else "#d44"
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>{html_escape(title)}</title>"
        "<body style=\"background:#0a0a0a;color:#e8e8e8;font-family:-apple-system,"
        "system-ui,sans-serif;padding:3rem 1.5rem;line-height:1.5\">"
        f"<h1 style='font-size:1.3rem;margin:0 0 .5rem;color:{colour}'>"
        f"{html_escape(title)}</h1>"
        f"<p style='color:#888;max-width:40rem'>{html_escape(detail)}</p>"
        "<p><a href='/' style='color:#1db954'>← Back to teCrawl</a></p></body>"
    )

def serve(port: int = 8765, open_browser: bool = True, bind: str = "0.0.0.0") -> int:
    """Default bind is all interfaces so the page stays reachable from phones
    over Tailscale/LAN (see README). Pass bind="127.0.0.1" for local-only."""
    out_dir = config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    # The OAuth redirect URI embeds the port, and Spotify matches it exactly,
    # so a non-default port needs its own Redirect URI on the Spotify app.
    spotify_connect.set_port(port)
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
            # Before the /<folder>/ fallback below, so an output folder that
            # happens to be named "queue" can't shadow the queue page.
            if path in ("/queue", "/queue/"):
                return self._send_queue()
            if path == "/api/discover":
                return self._api_discover(parsed.query)
            if path == "/api/pick-folder":
                return self._api_pick_folder()
            if path == "/api/feedback":
                return self._send_json(feedback.latest())
            if path == "/api/queue":
                return self._send_json({"keys": dlqueue.keys()})
            if path == "/api/spotify/status":
                return self._send_json(spotify_connect.status())
            if path == "/spotify/login":
                return self._spotify_login()
            if path == "/spotify/logout":
                spotify_connect.disconnect()
                return self._redirect("/")
            if path == "/callback":
                return self._spotify_callback(parsed.query)
            # /<folder> or /<folder>/ → list the runs in that folder
            parts = [p for p in path.strip("/").split("/") if p]
            if len(parts) == 1:
                sub = out_dir / parts[0]
                if sub.is_dir():
                    if not parsed.path.endswith("/"):
                        self.send_response(301)
                        self.send_header("Location", parsed.path + "/")
                        self.end_headers()
                        return
                    return self._send_folder(parts[0], sub)
            return super().do_GET()

        def do_POST(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            if path == "/api/feedback":
                return self._api_feedback_post()
            if path == "/api/queue":
                return self._api_queue_post()
            if path == "/api/more":
                return self._api_more()
            if path == "/api/stop":
                return self._api_stop()
            if path == "/api/spotify/play":
                return self._api_spotify_play()
            if path == "/api/spotify/pause":
                return self._api_spotify_cmd(spotify_connect.pause)
            if path == "/api/spotify/next":
                return self._api_spotify_cmd(spotify_connect.next_track)
            if path == "/api/spotify/previous":
                return self._api_spotify_cmd(spotify_connect.previous_track)
            self.send_error(404)

        # --- Spotify Connect -------------------------------------------
        # The embed player only ever gives a 30s preview unless its iframe
        # can read a first-party Spotify session, which browsers block. So
        # playback goes through the user's own Spotify client instead.

        def _redirect(self, location: str) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()

        def _spotify_login(self) -> None:
            try:
                self._redirect(spotify_connect.authorize_url())
            except spotify_connect.NotConnected as e:
                self._send_html(_notice_page("Can't start Spotify login", str(e)))

        def _spotify_callback(self, query: str) -> None:
            q = urllib.parse.parse_qs(query)
            if q.get("error"):
                return self._send_html(_notice_page(
                    "Spotify login cancelled", q["error"][0]))
            try:
                spotify_connect.exchange_code(
                    q.get("code", [""])[0], q.get("state", [""])[0])
            except (spotify_connect.ConnectError,
                    spotify_connect.NotConnected) as e:
                return self._send_html(_notice_page("Spotify login failed", str(e)))
            self._send_html(_notice_page(
                "Connected to Spotify",
                "Play buttons now drive your own Spotify player. "
                "You can close this tab.", ok=True))

        def _spotify_body(self) -> dict:
            n = min(int(self.headers.get("Content-Length") or 0), 10_000)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

        def _api_spotify_play(self) -> None:
            try:
                body = self._spotify_body()
                spotify_connect.play(
                    body.get("spotify_id", ""),
                    body.get("type") or "track",
                    body.get("device_id") or None,
                )
            except spotify_connect.NotConnected as e:
                return self._send_json(
                    {"error": str(e), "reason": "not_connected"}, status=401)
            except spotify_connect.NoActiveDevice as e:
                return self._send_json(
                    {"error": str(e), "reason": "no_device"}, status=409)
            except Exception as e:
                return self._send_json({"error": str(e)}, status=502)
            self._send_json({"ok": True})

        def _api_spotify_cmd(self, fn) -> None:
            try:
                fn()
            except spotify_connect.NotConnected as e:
                return self._send_json(
                    {"error": str(e), "reason": "not_connected"}, status=401)
            except spotify_connect.NoActiveDevice as e:
                return self._send_json(
                    {"error": str(e), "reason": "no_device"}, status=409)
            except Exception as e:
                return self._send_json({"error": str(e)}, status=502)
            self._send_json({"ok": True})

        def _api_feedback_post(self) -> None:
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 100_000)
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                stored = feedback.record(body)
            except (ValueError, UnicodeDecodeError) as e:
                self._send_json({"error": str(e)}, status=400)
                return
            self._send_json({"ok": True, "verdict": stored["verdict"]})

        def _api_queue_post(self) -> None:
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 100_000)
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                stored = dlqueue.record(body)
            except (ValueError, UnicodeDecodeError) as e:
                self._send_json({"error": str(e)}, status=400)
                return
            # The count rides back so the page can update its badge without a
            # second round trip.
            self._send_json({
                "ok": True,
                "action": stored["action"],
                "count": len(dlqueue.active()),
            })

        def _api_more(self) -> None:
            """Extend one already-rendered section with the next batch of
            candidates (the "Show more" button). Re-queries just that Discogs
            angle deeper, resolves the new rows to Spotify/YouTube, and returns
            them as ready-to-append HTML."""
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 200_000)
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            except (ValueError, UnicodeDecodeError) as e:
                self._send_json({"error": str(e)}, status=400)
                return

            source = (body.get("source") or "").strip()
            if source not in discover.MORE_SOURCES:
                self._send_json({"error": "unknown source"}, status=400)
                return

            def _int(v):
                try:
                    s = str(v).strip()
                    return int(s) if s else None
                except (TypeError, ValueError):
                    return None

            seed_artist = (body.get("seed_artist") or "").strip()
            seed_title = (body.get("seed_title") or "").strip()
            seed_primary = discover._primary_artist(seed_artist)
            styles = [s for s in (body.get("styles") or "").split("|") if s]
            shown: set[tuple[str, str]] = set()
            for pair in body.get("shown") or []:
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    shown.add((str(pair[0]).lower(), str(pair[1]).lower()))
            shown.add((seed_primary.lower(), seed_title.lower()))
            page = _int(body.get("page")) or 1
            batch = max(1, min(_int(body.get("batch")) or 10, 25))

            # Queue behind any running discovery: the API modules share
            # module-level throttle state, and both hammering Discogs at once
            # would trip the rate limit.
            with _RUN_LOCK:
                try:
                    cands, next_page, exhausted = discover.more_candidates(
                        source,
                        seed_primary=seed_primary,
                        label=(body.get("label") or "").strip() or None,
                        label_id=_int(body.get("label_id")),
                        artist_id=_int(body.get("artist_id")),
                        release_id=_int(body.get("release_id")),
                        styles=styles,
                        shown=shown,
                        page=page,
                        batch=batch,
                    )
                    cands = discover.resolve_to_spotify(cands)
                    cands = discover.resolve_to_youtube(cands)
                except Exception as e:
                    self._send_json({"error": f"more failed: {e}"}, status=500)
                    return

            self._send_json({
                "rows": render.render_more_rows(seed_artist, seed_title, cands),
                "count": len(cands),
                "next_page": next_page,
                "exhausted": exhausted,
            })

        def _api_stop(self) -> None:
            """Signal cancellation for whichever discovery run currently
            holds _RUN_LOCK. A harmless no-op if nothing is running (or the
            run already finished) — the flag is cleared again at the start
            of the next run either way."""
            _CANCEL_EVENT.set()
            self._send_json({"ok": True})

        def _send_html(self, html_text: str) -> None:
            data = html_text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, obj: dict, status: int = 200) -> None:
            data = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_index(self) -> None:
            # Most-recently-active folders first, so today's digging is at the
            # top instead of buried under alphabetically-earlier old playlists.
            folders = sorted(
                (_folder_summary(p) for p in out_dir.iterdir()
                 if p.is_dir() and any(p.glob("*.html"))),
                key=lambda f: f["sort"],
                reverse=True,
            )
            self._send_html(
                render.render_template(
                    "index.html.j2",
                    folders=folders,
                    queue_count=len(dlqueue.active()),
                    server_started=server_started,
                )
            )

        def _send_queue(self) -> None:
            self._send_html(
                render.render_template(
                    "queue.html.j2",
                    entries=dlqueue.active(),
                    server_started=server_started,
                )
            )

        def _send_folder(self, name: str, sub: Path) -> None:
            runs = _folder_runs(sub)
            if not runs:
                self.send_error(404, "No runs in this playlist yet")
                return
            self._send_html(
                render.render_template(
                    "folder.html.j2",
                    folder=name,
                    runs=runs,
                    server_started=server_started,
                )
            )

        def _send_search(self, query: str) -> None:
            qs = urllib.parse.parse_qs(query)
            q = (qs.get("q") or [""])[0]
            fresh = _truthy((qs.get("fresh") or [""])[0])
            self._send_html(render.render_template(
                "search.html.j2", q=q, fresh=fresh,
                server_started=server_started,
            ))

        def _api_pick_folder(self) -> None:
            path, err = _pick_folder()
            body: dict = {}
            if path:
                body["path"] = path
            elif err:
                body["error"] = err
            self._send_json(body)

        def _api_discover(self, query: str) -> None:
            qs = urllib.parse.parse_qs(query)
            q = (qs.get("q") or [""])[0].strip()
            fresh = _truthy((qs.get("fresh") or [""])[0])

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
                # Fresh lock holder: clear any stale cancel from a previous
                # (possibly stopped) run before this one can be flagged.
                _CANCEL_EVENT.clear()
                # Bypass is a module global in cache; set it only while we hold
                # the single-run lock so concurrent requests can't clobber it.
                cache.set_bypass(fresh)
                if fresh:
                    emit({"type": "note",
                          "message": "Fresh dig — skipping cache, re-fetching every source."})
                try:
                    if folder is not None:
                        self._run_folder(folder, emit, _CANCEL_EVENT)
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
                        cancel=_CANCEL_EVENT,
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
                    cache.set_bypass(False)
                    _RUN_LOCK.release()
            except discover.Cancelled:
                try:
                    emit({"type": "stopped",
                          "elapsed": round(time.time() - t0, 1)})
                except OSError:
                    pass
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

        def _run_folder(self, folder, emit, cancel) -> None:
            """Multi-seed run over a local folder: each seed's results block
            streams to the page the moment that seed finishes. If the tab
            closes mid-run, the current seed completes, everything done so
            far still persists to output/ (the API work is already spent),
            and the run stops instead of grinding through the rest.

            `cancel` (a `threading.Event`, set by /api/stop): checked before
            each seed and passed into the discovery calls, which raise
            `discover.Cancelled` between their own API hops — so a Stop click
            interrupts the *current* seed too rather than waiting for it to
            finish. Seeds already streamed via seed_html before the stop are
            kept; the interrupted seed is dropped, not added half-done."""
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
            run_seen: set = set()  # rotates "Same vibe" across seeds
            for i, st in enumerate(fscan.tracks, 1):
                if dead[0] or cancel.is_set():
                    break
                prefix = f"[{i}/{n}] {st.track.artist} — {st.track.title}"
                status(f"{prefix} · digging…")
                say = lambda m, _p=prefix: status(f"{_p} · {m}")
                release = None
                try:
                    release, cands = discover.discover_for_seed(
                        st.track, progress=say, run_seen=run_seen, cancel=cancel
                    )
                    cands = discover.resolve_to_spotify(
                        cands, progress=say, cancel=cancel
                    )
                    cands = discover.resolve_to_youtube(
                        cands, progress=say, cancel=cancel
                    )
                except discover.Cancelled:
                    break
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

            stopped = cancel.is_set()
            if not seed_blocks:
                # Stopped before any seed finished — nothing worth persisting.
                say_json({
                    "type": "stopped",
                    "elapsed": round(time.time() - t0, 1),
                    "seeds": 0,
                    "candidates": 0,
                })
                return

            top_picks = discover.aggregate_top_picks(
                seed_blocks, min_hits=discover.default_min_hits(len(seed_blocks))
            )
            out_path = render.render(
                seed_blocks, top_picks=top_picks, playlist_name=fscan.name
            )
            rel = out_path.relative_to(config.OUTPUT_DIR)
            say_json({
                "type": "stopped" if stopped else "done",
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
