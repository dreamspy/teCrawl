"""Stop button (TODO: "No way to stop a running dig").

Exercises the real cancellation mechanism end-to-end:

  - `discover.discover_for_seed` / `resolve_to_spotify` / `resolve_to_youtube`
    unwind at their next say()/progress() checkpoint once `cancel` is set,
    without running the remaining discovery angles / candidates (deterministic
    call-count checks, no sleeping).
  - The real `web.serve()` HTTP+SSE server, driven exactly like a browser
    would drive it: `POST /api/stop` on a live `/api/discover` stream halts a
    quick single-track dig promptly instead of letting it run to completion,
    a normal (unstopped) dig still completes unchanged, a folder run keeps
    the seeds it already streamed and marks itself "stopped early" instead of
    "done", and stopping releases `_RUN_LOCK` immediately so a queued second
    request proceeds right after.

No real Discogs/Last.fm/Spotify/YouTube credentials are needed or used —
every network-facing function discover.py/seed_input.py call is replaced
with an in-memory fake.
"""

import http.client
import json
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from tempfile import TemporaryDirectory

from tecrawl import config

# Redirect all disk writes before importing anything that captures the path
# at import/serve time.
_tmp = TemporaryDirectory()
config.OUTPUT_DIR = Path(_tmp.name) / "output"
config.CACHE_DIR = Path(_tmp.name) / "cache"
config.DISCOGS_TOKEN = "test-token"
config.LASTFM_API_KEY = "test-key"

from tecrawl import discogs, discogs_scrape, discover, lastfm, localfiles, quick, spotify, web, youtube  # noqa: E402


def fake_release(artist="Seed Artist", title="Seed Track"):
    return discogs.Release(
        title=title, artist=artist, label="Test Label", label_id=1,
        artist_id=2, release_id=100, styles=["Techno"], year=2020, exact=True,
    )


class Fakes:
    """Swaps every network call discover.py/seed_input.py can make for an
    in-memory fake, and counts invocations so tests can assert *which*
    angles ran without depending on wall-clock timing."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = {
            "search_release": 0, "label_releases": 0, "artist_releases": 0,
            "style_recommendations": 0, "scrape_recommendations": 0,
            "similar_tracks": 0, "similar_artists": 0, "artist_top_tracks": 0,
            "search_track": 0, "search_album": 0, "search_video_id": 0,
        }
        self._patches = []

    def _sleep(self):
        if self.delay:
            time.sleep(self.delay)

    def install(self):
        def p(obj, name, fn):
            orig = getattr(obj, name)
            setattr(obj, name, fn)
            self._patches.append((obj, name, orig))

        def search_release(artist, title):
            self.calls["search_release"] += 1
            self._sleep()
            return fake_release(artist, title)

        def label_releases(label_id, per_page=30, page=1):
            self.calls["label_releases"] += 1
            self._sleep()
            return []

        def artist_releases(artist_id, per_page=15, page=1):
            self.calls["artist_releases"] += 1
            self._sleep()
            return []

        def style_recommendations(release, limit=30):
            self.calls["style_recommendations"] += 1
            self._sleep()
            return []

        def scrape_recommendations(release_id, limit=10, cancel=None):
            self.calls["scrape_recommendations"] += 1
            self._sleep()
            return []

        def similar_tracks(artist, title, limit=10):
            self.calls["similar_tracks"] += 1
            self._sleep()
            return []

        def similar_artists(artist, limit=4):
            self.calls["similar_artists"] += 1
            self._sleep()
            return []

        def artist_top_tracks(artist, limit=2):
            self.calls["artist_top_tracks"] += 1
            return []

        def search_track(artist, title):
            self.calls["search_track"] += 1
            return spotify.Track(artist=artist, title=title, spotify_id="seed-sp-id")

        def search_album(artist, title):
            self.calls["search_album"] += 1
            return spotify.Album(artist=artist, title=title, spotify_id="seed-sp-album-id")

        def search_video_id(artist, title):
            self.calls["search_video_id"] += 1
            return "seed-yt-id"

        p(discogs, "search_release", search_release)
        p(discogs, "label_releases", label_releases)
        p(discogs, "artist_releases", artist_releases)
        p(discogs, "style_recommendations", style_recommendations)
        p(discogs_scrape, "recommendations", scrape_recommendations)
        p(lastfm, "similar_tracks", similar_tracks)
        p(lastfm, "similar_artists", similar_artists)
        p(lastfm, "artist_top_tracks", artist_top_tracks)
        p(spotify, "search_track", search_track)
        p(spotify, "search_album", search_album)
        p(youtube, "search_video_id", search_video_id)
        return self

    def uninstall(self):
        for obj, name, orig in self._patches:
            setattr(obj, name, orig)
        self._patches = []


class DiscoverCheckpointTests(unittest.TestCase):
    """Deterministic (no sleeping) proof that the cooperative cancel flag is
    checked at the say()/progress() checkpoints between API hops, and that
    setting it mid-run stops *before* the remaining angles run — not just
    eventually, and not by letting the whole seed finish first."""

    def setUp(self):
        self.fakes = Fakes().install()
        self.addCleanup(self.fakes.uninstall)

    def test_discover_for_seed_stops_before_remaining_angles(self):
        cancel = threading.Event()
        seen_messages = []

        def progress(m):
            seen_messages.append(m)
            # Trip cancel right after the checkpoint that precedes the
            # label_releases call (the 3rd say(): "looking up the
            # release…", "found …", "other releases on <label>…"). The
            # *next* say() — before artist_releases — is where the flag
            # gets noticed, so label_releases still runs but nothing after
            # it does.
            if len(seen_messages) == 3:
                cancel.set()

        seed = spotify.Track(artist="Seed Artist", title="Seed Track", spotify_id=None)
        with self.assertRaises(discover.Cancelled):
            discover.discover_for_seed(seed, progress=progress, cancel=cancel)

        # Made real progress before stopping...
        self.assertEqual(self.fakes.calls["search_release"], 1)
        self.assertEqual(self.fakes.calls["label_releases"], 1)
        # ...but did NOT run the remaining angles to completion.
        self.assertEqual(self.fakes.calls["artist_releases"], 0)
        self.assertEqual(self.fakes.calls["style_recommendations"], 0)
        self.assertEqual(self.fakes.calls["scrape_recommendations"], 0)
        self.assertEqual(self.fakes.calls["similar_tracks"], 0)
        self.assertEqual(self.fakes.calls["similar_artists"], 0)

    def test_discover_for_seed_uncancelled_runs_every_angle(self):
        """Regression guard: with cancel never set, every angle still runs —
        the checkpoint wrapper must be a no-op when the flag is clear."""
        seed = spotify.Track(artist="Seed Artist", title="Seed Track", spotify_id=None)
        discover.discover_for_seed(seed, progress=lambda m: None, cancel=threading.Event())
        self.assertEqual(self.fakes.calls["search_release"], 1)
        self.assertEqual(self.fakes.calls["label_releases"], 1)
        self.assertEqual(self.fakes.calls["artist_releases"], 1)
        self.assertEqual(self.fakes.calls["style_recommendations"], 1)
        self.assertEqual(self.fakes.calls["scrape_recommendations"], 1)
        self.assertEqual(self.fakes.calls["similar_tracks"], 1)
        self.assertEqual(self.fakes.calls["similar_artists"], 1)

    def test_resolve_to_spotify_stops_between_candidates(self):
        cancel = threading.Event()
        candidates = [
            discover.Candidate(f"Artist {i}", f"Title {i}", "discogs_artist", "", None, None, None)
            for i in range(5)
        ]
        calls = []

        def progress(m):
            calls.append(m)
            if len(calls) == 2:  # after the 1st candidate's checkpoint
                cancel.set()

        with self.assertRaises(discover.Cancelled):
            discover.resolve_to_spotify(candidates, progress=progress, cancel=cancel)
        # search_track isn't on the resolve_to_spotify path (it uses
        # search_album/search_track for albums/tracks) — assert via message
        # count instead: far fewer than 5 checkpoints fired.
        self.assertLess(len(calls), 5)

    def test_resolve_to_youtube_stops_between_candidates(self):
        cancel = threading.Event()
        candidates = [
            discover.Candidate(f"Artist {i}", f"Title {i}", "lastfm_track", "", None, None, None)
            for i in range(5)
        ]
        calls = []

        def progress(m):
            calls.append(m)
            if len(calls) == 2:
                cancel.set()

        with self.assertRaises(discover.Cancelled):
            discover.resolve_to_youtube(candidates, progress=progress, cancel=cancel)
        self.assertLess(self.fakes.calls["search_video_id"], 5)


class SeedResolutionCancelTests(unittest.TestCase):
    """Reproduces the review-round gap in the free-text quick-search path:
    seed_input._seed_from_text() (no ' - ' in the query) chains up to three
    sequential network calls — Spotify freetext search, then Last.fm search,
    then Spotify canonicalize — with no say()/progress() message between the
    first two. Before this fix, quick.run() never forwarded `cancel` into
    seed_input.resolve() at all, so a Stop click during seed resolution went
    unnoticed until it returned and discover_for_seed's own checkpoints took
    over — up to ~45s of unstoppable network calls on a slow provider."""

    def setUp(self):
        self.fakes = Fakes().install()
        self.addCleanup(self.fakes.uninstall)
        self.calls = {"search_track_freetext": 0, "lastfm_search_track": 0}
        self._orig_freetext = spotify.search_track_freetext
        self._orig_lastfm_search = lastfm.search_track
        self.addCleanup(self._restore)

    def _restore(self):
        spotify.search_track_freetext = self._orig_freetext
        lastfm.search_track = self._orig_lastfm_search

    def test_cancel_set_during_freetext_call_stops_before_lastfm_fallback(self):
        cancel = threading.Event()

        def search_track_freetext(q):
            self.calls["search_track_freetext"] += 1
            cancel.set()  # simulate Stop clicked while this call was in flight
            return None

        def lastfm_search_track(query):
            self.calls["lastfm_search_track"] += 1
            return ("Fallback Artist", "Fallback Title")

        spotify.search_track_freetext = search_track_freetext
        lastfm.search_track = lastfm_search_track

        with self.assertRaises(discover.Cancelled):
            quick.run("no dash free text query", cancel=cancel)

        self.assertEqual(self.calls["search_track_freetext"], 1)
        # The checkpoint before the Last.fm fallback must catch the flag —
        # not run the fallback call to completion first.
        self.assertEqual(self.calls["lastfm_search_track"], 0)
        # Cancellation was caught during seed resolution, before discovery
        # (discogs.search_release, the first call in discover_for_seed) ever
        # started.
        self.assertEqual(self.fakes.calls["search_release"], 0)

    def test_already_cancelled_stops_with_zero_network_calls(self):
        cancel = threading.Event()
        cancel.set()

        def search_track_freetext(q):
            self.calls["search_track_freetext"] += 1
            return None

        spotify.search_track_freetext = search_track_freetext

        with self.assertRaises(discover.Cancelled):
            quick.run("no dash free text query", cancel=cancel)

        self.assertEqual(self.calls["search_track_freetext"], 0)
        self.assertEqual(self.calls["lastfm_search_track"], 0)
        self.assertEqual(self.fakes.calls["search_release"], 0)

    def test_uncancelled_freetext_lookup_still_resolves_normally(self):
        """Regression guard: with cancel never set, the free-text fallback
        chain still runs end-to-end and discovery still proceeds."""
        def search_track_freetext(q):
            self.calls["search_track_freetext"] += 1
            return None

        def lastfm_search_track(query):
            self.calls["lastfm_search_track"] += 1
            return ("Fallback Artist", "Fallback Title")

        spotify.search_track_freetext = search_track_freetext
        lastfm.search_track = lastfm_search_track

        result = quick.run(
            "no dash free text query", cancel=threading.Event(), persist=False
        )
        self.assertEqual(self.calls["search_track_freetext"], 1)
        self.assertEqual(self.calls["lastfm_search_track"], 1)
        self.assertEqual(result.track.artist, "Fallback Artist")
        self.assertEqual(self.fakes.calls["search_release"], 1)


HOST = "127.0.0.1"


def _find_free_port() -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SSEReader(threading.Thread):
    """Reads Server-Sent Events off a live /api/discover connection exactly
    like the browser's EventSource does, timestamping each event relative to
    a caller-supplied t0 so cross-connection timing (e.g. "how long after I
    clicked Stop did the 'stopped' event arrive") is comparable."""

    def __init__(self, port, path, t0):
        super().__init__(daemon=True)
        self.port = port
        self.path = path
        self.t0 = t0
        self.events: list[tuple[float, dict]] = []
        self.done = threading.Event()
        self.error = None

    def run(self):
        try:
            conn = http.client.HTTPConnection(HOST, self.port, timeout=30)
            conn.request("GET", self.path)
            resp = conn.getresponse()
            while True:
                line = resp.readline()
                if not line:
                    break
                line = line.decode("utf-8").rstrip("\n")
                if line.startswith("data: "):
                    obj = json.loads(line[len("data: "):])
                    self.events.append((time.monotonic() - self.t0, obj))
                    if obj.get("type") in ("result", "done", "stopped", "error"):
                        break
            conn.close()
        except Exception as e:  # pragma: no cover - diagnostic aid only
            self.error = e
        finally:
            self.done.set()

    def types(self):
        return [e.get("type") for _, e in self.events]

    def last(self, type_):
        for t, e in reversed(self.events):
            if e.get("type") == type_:
                return t, e
        return None


def post_stop(port: int) -> None:
    conn = http.client.HTTPConnection(HOST, port, timeout=10)
    conn.request("POST", "/api/stop")
    resp = conn.getresponse()
    resp.read()
    conn.close()


class WebServerStopTests(unittest.TestCase):
    """Drives the real web.serve() server over actual HTTP/SSE — the same
    transport the browser's Stop button uses — with the network layer
    replaced by artificial-delay fakes so timing assertions are about the
    cancellation mechanism, not real API latency."""

    DELAY = 0.35  # per fake network call

    @classmethod
    def setUpClass(cls):
        cls.port = _find_free_port()
        cls.thread = threading.Thread(
            target=web.serve,
            kwargs=dict(port=cls.port, open_browser=False, bind=HOST),
            daemon=True,
        )
        cls.thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                conn = http.client.HTTPConnection(HOST, cls.port, timeout=1)
                conn.request("GET", "/")
                conn.getresponse().read()
                conn.close()
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("test server never came up")

    def setUp(self):
        self.fakes = Fakes(delay=self.DELAY).install()
        self.addCleanup(self.fakes.uninstall)

    def test_quick_dig_stop_halts_promptly_not_after_full_completion(self):
        q = urllib.parse.quote("Stoptest Artist - Stoptest Track")
        t0 = time.monotonic()
        reader = SSEReader(self.port, f"/api/discover?q={q}", t0)
        reader.start()

        # Let it get partway through discover_for_seed (search_release +
        # label_releases have time to fire; the remaining ~5 checkpoints
        # have not) before clicking Stop.
        time.sleep(self.DELAY * 2 + 0.15)
        click_t = time.monotonic() - t0
        post_stop(self.port)

        self.assertTrue(reader.done.wait(10), "SSE stream never finished")
        self.assertIsNone(reader.error)
        self.assertIn("stopped", reader.types())
        stopped_t, _ = reader.last("stopped")

        # Halted promptly: the gap between clicking Stop and the 'stopped'
        # event landing is small — at most one more in-flight fake call, not
        # the ~7 remaining checkpoints' worth of delay.
        self.assertLess(stopped_t - click_t, self.DELAY * 3)
        # And it did not run every angle to completion.
        self.assertLess(self.fakes.calls["style_recommendations"]
                         + self.fakes.calls["scrape_recommendations"]
                         + self.fakes.calls["similar_tracks"]
                         + self.fakes.calls["similar_artists"], 4)

    def test_quick_dig_uncancelled_still_completes_normally(self):
        """Regression: a dig nobody stops must still finish with a normal
        'result' event, unchanged from before this feature."""
        q = urllib.parse.quote("Normaltest Artist - Normaltest Track")
        reader = SSEReader(self.port, f"/api/discover?q={q}", time.monotonic())
        reader.start()
        self.assertTrue(reader.done.wait(15), "SSE stream never finished")
        self.assertIsNone(reader.error)
        self.assertIn("result", reader.types())
        self.assertNotIn("stopped", reader.types())
        _, result = reader.last("result")
        self.assertIsNotNone(result.get("permalink"))
        # Every angle ran — nothing was cut short.
        self.assertEqual(self.fakes.calls["style_recommendations"], 1)
        self.assertEqual(self.fakes.calls["similar_artists"], 1)

    def test_folder_run_stop_keeps_completed_seeds_and_marks_stopped(self):
        seeds = [
            localfiles.ScannedTrack(
                track=spotify.Track(artist=f"Folder Artist {i}", title=f"Folder Track {i}", spotify_id=None),
                how="tags", path=Path(f"/fake/{i}.mp3"),
            )
            for i in range(5)
        ]
        scan_result = localfiles.FolderScan(name="Stop Folder Test", tracks=seeds, skipped=[])
        orig_scan = localfiles.scan
        localfiles.scan = lambda folder, cancel=None: scan_result
        self.addCleanup(lambda: setattr(localfiles, "scan", orig_scan))

        with TemporaryDirectory() as d:
            q = urllib.parse.quote(d)
            t0 = time.monotonic()
            reader = SSEReader(self.port, f"/api/discover?q={q}", t0)
            reader.start()

            # 7 delayed network calls/seed (search_release, label_releases,
            # artist_releases, style_recommendations, scrape recs, 2x
            # lastfm) * DELAY ~= 2.45s/seed, plus HTTP/thread overhead.
            # Sleep past seed 1's completion, then partway into seed 2, so
            # exactly 1 seed has fully streamed when Stop is clicked.
            time.sleep(self.DELAY * 9 + 0.6)
            post_stop(self.port)

            self.assertTrue(reader.done.wait(20), "SSE stream never finished")
            self.assertIsNone(reader.error)

        seed_html_events = [e for _, e in reader.events if e.get("type") == "seed_html"]
        self.assertEqual(len(seed_html_events), 1,
                          f"expected exactly 1 completed seed before stop, got types={reader.types()}")
        self.assertIn("stopped", reader.types())
        self.assertNotIn("done", reader.types())
        _, stopped = reader.last("stopped")
        self.assertEqual(stopped.get("seeds"), 1)
        permalink = stopped.get("permalink")
        self.assertIsNotNone(permalink, "partial folder page must still be persisted")

        # The persisted page on disk actually contains the 1 completed seed
        # (and only that one) — the "partial results stay on the page"
        # requirement, verified against the real render() output.
        on_disk = config.OUTPUT_DIR / urllib.parse.unquote(permalink).lstrip("/")
        html_text = on_disk.read_text(encoding="utf-8")
        self.assertIn("Folder Artist 0", html_text)
        for i in range(1, 5):
            self.assertNotIn(f"Folder Artist {i}", html_text)

    def test_folder_run_uncancelled_still_completes_with_all_seeds(self):
        seeds = [
            localfiles.ScannedTrack(
                track=spotify.Track(artist=f"NormalFolder Artist {i}", title=f"NormalFolder Track {i}", spotify_id=None),
                how="tags", path=Path(f"/fake/{i}.mp3"),
            )
            for i in range(2)
        ]
        scan_result = localfiles.FolderScan(name="Normal Folder Test", tracks=seeds, skipped=[])
        orig_scan = localfiles.scan
        localfiles.scan = lambda folder, cancel=None: scan_result
        self.addCleanup(lambda: setattr(localfiles, "scan", orig_scan))

        with TemporaryDirectory() as d:
            q = urllib.parse.quote(d)
            reader = SSEReader(self.port, f"/api/discover?q={q}", time.monotonic())
            reader.start()
            self.assertTrue(reader.done.wait(20), "SSE stream never finished")

        self.assertIsNone(reader.error)
        seed_html_events = [e for _, e in reader.events if e.get("type") == "seed_html"]
        self.assertEqual(len(seed_html_events), 2)
        self.assertIn("done", reader.types())
        self.assertNotIn("stopped", reader.types())
        _, done = reader.last("done")
        self.assertEqual(done.get("seeds"), 2)

    def test_stop_releases_lock_so_queued_run_proceeds_immediately(self):
        """A run blocked behind _RUN_LOCK (reporting 'queued…') must proceed
        right after the active run is stopped, not wait for anything else."""
        q1 = urllib.parse.quote("Locktest Artist One - Locktest Track One")
        q2 = urllib.parse.quote("Locktest Artist Two - Locktest Track Two")

        t0 = time.monotonic()
        reader_a = SSEReader(self.port, f"/api/discover?q={q1}", t0)
        reader_a.start()
        # Let A actually acquire _RUN_LOCK and get into discover_for_seed.
        time.sleep(self.DELAY * 1 + 0.1)

        reader_b = SSEReader(self.port, f"/api/discover?q={q2}", t0)
        reader_b.start()
        # Give B time to hit the lock and emit its "queued…" status.
        time.sleep(0.2)
        self.assertTrue(
            any(e.get("type") == "status" and "queued" in e.get("message", "").lower()
                for _, e in reader_b.events),
            f"B should have reported queued, got {reader_b.events}",
        )

        stop_click_t = time.monotonic() - t0
        post_stop(self.port)

        self.assertTrue(reader_a.done.wait(10), "run A (stopped) never finished")
        self.assertIn("stopped", reader_a.types())
        a_stopped_t, _ = reader_a.last("stopped")

        self.assertTrue(reader_b.done.wait(15), "run B (queued) never finished")
        self.assertIsNone(reader_b.error)
        self.assertIn("result", reader_b.types())
        b_result_t, _ = reader_b.last("result")

        # B must complete promptly after A releases the lock — not stall
        # behind a stopped run nobody wants anymore.
        self.assertLess(b_result_t - a_stopped_t, self.DELAY * 12 + 2,
                         "queued run B took too long to finish after A stopped")
        self.assertGreater(a_stopped_t, 0)
        self.assertGreater(stop_click_t, 0)


if __name__ == "__main__":
    unittest.main()
