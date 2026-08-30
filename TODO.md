# TODO

## v2 — high-leverage next steps

- [x] **No way to stop a running dig — needs a Stop button** (user report 2026-08-28, shipped 2026-08-30): `discover.Cancelled` + a `cancel` param threaded through `discover_for_seed`, `resolve_to_spotify`, `resolve_to_youtube` and `quick.run` — each wraps its `say()`/`progress()` checkpoint to raise `Cancelled` once the flag is set, so a stop is noticed between API hops rather than only at the next `emit()`. `web.py` keeps one module-level `_CANCEL_EVENT`, cleared right after a run acquires `_RUN_LOCK`; a new `POST /api/stop` just sets it — no lock needed, since the run's own `finally` releases `_RUN_LOCK` as it unwinds, which is what lets a queued second request proceed right after. `_run_folder`'s per-seed loop catches `Cancelled` and `break`s (the interrupted seed is dropped, not appended half-done), then emits `type: "stopped"` instead of `"done"` with whatever seeds already streamed via `seed_html` — that partial page still gets written to `output/`. The quick single-track path has no partial concept, so its `Cancelled` just surfaces a bare `"stopped"` event. `search.html.j2` (the only page with a live SSE status bar — `index.html.j2` and `folder.html.j2` don't stream) gets a **■ Stop** button next to the spinner, an amber `.status.stopped` state, and a `stopped` SSE handler. Verified with a mocked-network integration harness (no real API keys needed) plus a live browser run: stopping a quick dig landed in ~2s vs. ~7-16s uncancelled; stopping a folder run kept the 4 already-completed seeds (dropping the interrupted 5th) and wrote the partial archive page; a queued second request proceeded immediately once the first was stopped; both normal (unstopped) paths still complete exactly as before.
  - **Two more uninterruptible waits turned up in real usage** (same day): `discogs_scrape.recommendations()` launches a real headless Chromium to clear Discogs' Cloudflare challenge before scraping, and that wait alone can block up to 30s in one call with no checkpoint inside it — `_pass_challenge()` now checks the cancel flag on its existing 1s title-poll, landing a stop in ~1s instead. And `localfiles.scan()` (reading tags off every file before a folder dig's per-seed loop even starts) had no checkpoint at all; a 183-track folder on Dropbox CloudStorage measured ~29s just to scan, fully unstoppable — `scan()` now takes a `cancel` flag and checks it once per file. `Cancelled` moved to its own `cancel.py` module so both can raise/catch it without a circular import through `discover.py`.

- [ ] **No visibility or control over dig requests queued behind `_RUN_LOCK`** (follow-up to the Stop button item above, 2026-08-30): checked the acquire path in `web.py` — a second `/api/discover` while a dig is running hits `_RUN_LOCK.acquire(blocking=False)`, fails, emits a one-shot `{"type": "status", "message": "Another search is running · queued…"}` over its *own* SSE stream, then calls the plain blocking `_RUN_LOCK.acquire()` and just sits there. There is no shared record of who's waiting: no list, no count, no per-queued-request identity anywhere in `web.py` — a second tab has no way to see that a queue exists, what's in it, or its own position, and once queued there is no way to back out short of closing the tab (which, per the Stop button item, doesn't even free the lock promptly if the active run is mid-step). This is distinct from stopping the *active* dig: it's about the request(s) sitting behind it that haven't started yet.
  - **Needs a shared queue structure**, not just a bare blocking `acquire()` — e.g. a module-level list/dict of pending requests (a generated id, seed/folder description, queued-at timestamp) that a waiter registers itself into before blocking on the lock and removes itself from once it acquires (or is cancelled). `_RUN_LOCK` itself can stay a plain `Lock`; the new state just needs its own guard (or reuse the lock's critical sections) since multiple waiters could register concurrently.
  - **Visibility**: a small "N dig(s) waiting" indicator (index/search/folder pages, near wherever the Stop button lands) that lists the queued items — enough to tell one queued request from another (seed/folder name), not just a count.
  - **Cancel a queued item**: a per-item ✕ that removes it from the pending list before it starts running. Needs a distinct code path from the Stop button's cancel-flag mechanism, since a queued request hasn't acquired `_RUN_LOCK` yet and has no run in progress to interrupt — cancelling here just means "never call `acquire()` for this one" and returning/closing its SSE stream with a "cancelled while queued" status instead of running the dig at all.
  - **Ordering**: `Lock.acquire()` doesn't guarantee FIFO wakeup order among blocked waiters, so if per-item cancel needs to reason about "who's next" (vs. just "is this id still pending"), that may need an explicit FIFO structure (e.g. a queue of ids checked before each acquire) rather than relying on the lock's own wakeup order.

- [ ] **In-app "add a TODO" button — a self-reported inbox for feature ideas / bugs** (user request 2026-08-28): a quick-capture control (💡 or ✎, footer or header, present on every page) that pops a small text box and appends the note to a new local file — a personal inbox, not a replacement for this curated TODO.md. Right now every item in this file gets written up by hand mid-conversation; this is for the ones that occur mid-browsing when no one's around to write it down for you.
  - **Storage**: same append-only JSONL pattern as `dlqueue.py`/`feedback.py` — `todo_inbox.jsonl` at the project root, one line per note (`ts`, `text`, optionally `page`/`seed` context if the button was clicked from a candidate row rather than a generic nav spot), `action` field (`add`/`resolve`) so entries can be marked handled without ever rewriting history.
  - **Viewing**: an `/inbox` page listing open entries newest-first (mirrors `/queue`'s structure) with a manual **×** to mark one resolved once it's been folded into TODO.md or fixed.
  - **Folding into TODO.md**: manual for now — periodically read `/inbox` (or the file), triage into this file the way every other entry here was written, then resolve the source entries. Auto-promoting raw notes into curated TODO.md entries isn't worth automating until there's real volume to justify it.
  - **Scope check**: generic free text, not structured feature-vs-bug — a raw capture point beats a form with fields nobody fills in under time pressure.

- [ ] **Spotify ▶ always grayed out, even on tracks that exist on Spotify** (user report 2026-08-28): checked live — `.env` has `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` set, but the token request fails with "Spotify app credentials rejected" (`spotify._get_token()`). So every candidate resolves with `spotify_id=None` regardless of whether the track is actually on Spotify, and `_cand_row.html.j2` / `_seed_block.html.j2` / `_top_picks.html.j2` all render `class="play disabled"` off that. Not a resolution bug: the resolver never successfully searches Spotify at all right now. Fixing the credentials (see "Create a new Spotify app and test the resolver fix" further down) should make this go away; if the button is *still* grayed out on a findable track after fresh credentials are in place, that would point to a genuine matching bug in `resolve_to_spotify` / `spotify.search_track` / `search_album` worth revisiting on its own.

- [x] **Media keys are unreliable** (user report 2026-08-28, fixed 2026-08-30 in `8d692ff`): silent-keeper clip lengthened 0.1s → 6s and made to mirror play/pause state, a new `claimMediaKeys()` re-asserts the OS media session after every YouTube `onStateChange` play event, `pauseCurrent()` now pauses the keeper and syncs `mediaSession.playbackState`, and a manual row ▶ (`playRowAsQueue()`) joins the play-all queue instead of orphaning it — all in `src/tecrawl/templates/_player.js`. Addresses every suspect below except the autoplay-policy one, which the surrounding fixes make largely moot for the play-all path. Suspects that motivated the fix, in rough order of likelihood:
  - The silent-keeper `<audio>` (`startSilentKeeper`) is what holds the OS media session; the YouTube iframe competes for it, and whichever element played most recently tends to win. If the keeper gets paused, garbage-collected, or its `play()` promise rejects (the `.catch(function () {})` swallows it silently), the keys go to the iframe and `nexttrack` / `previoustrack` get swallowed.
  - `queueStop` pauses the keeper, so keys are dead until the next Play-all click. Expected, but worth confirming that's what the user is hitting rather than a genuine dropout mid-queue.
  - `navigator.mediaSession.playbackState` is only set in a few places; if it drifts out of sync with the actual player, some browsers stop routing keys.
  - Autoplay policy: the keeper is started from the click gesture, so a queue resumed any other way may never get it playing.
  - First step is diagnosis, not a fix: log every media-key action and the keeper's `paused` / `currentTime` state to the console so the failure mode is identifiable when it happens, and note whether it correlates with tab backgrounding, a dead embed being skipped, or a long-running queue.

- [x] **A real "open in Spotify" button** (user request 2026-08-28, shipped 2026-08-28): `SPT↗` in the same `.ext` chip style as the other destinations, leading the strip on candidate rows, top picks, the seed header (which had no Spotify affordance at all before) and the download-queue page. The label no longer flips between `Spotify` and `Search` — it is always `SPT↗`, and the unresolved state is shown by dimming (`.ext.spt.secondary`) so the strip never reflows. `Dig↗` moved from the Spotify green to violet `#b46ad4`, since it is teCrawl's own action rather than an external service and was otherwise indistinguishable from the new green `SPT↗`. Seed URLs go through a new `render.spotify_track_url()` global (`spotify.Track` carries only a bare id, no `spotify_url`).
- [x] **Quick "add to download queue" button per row** ✅ shipped 2026-08-28 — ⬇ on every candidate row, top pick and seed header, appending to `download_queue.jsonl` via `/api/queue`, listed at `/queue`
  - **Storage decided: a local JSONL, not a Spotify playlist.** The Spotify route needs an app that can't be created yet plus user OAuth with `playlist-modify-private`, and would capture near-zero tracks while resolution is broken — and never covers YouTube-only finds. `src/tecrawl/dlqueue.py` mirrors `feedback.py`: append-only, latest action per (artist, title) wins, `add`/`remove`, so the full history stays auditable while `active()` returns just what's waiting
  - **copy** button on every row everywhere (candidates, top picks, seed header, and both buttons on `/queue`), sharing one clipboard helper in `_player.js` so a single track and a bulk copy can't produce different text
  - `/queue` page: rows reuse the `.cand` markup, so the row grid, YouTube playback, ▶ Play all and the transport bar all work there with no new code. Shows the seed each track came from, a **copy** button per row plus a **Copy list** button for the lot (one `Artist - Title` per line; the async clipboard API on localhost, a textarea fallback when reached by LAN IP or Tailscale name over plain HTTP), and a manual **×** per entry
  - The ⬇ shares the `.fb` span's dataset rather than duplicating per-row data attributes; `applyRowStates()` replaces the four scattered `applyFeedbackStates()` calls so SSE-injected and "Show more" rows repaint both states from one hook
  - Not built: no `tecrawl queue` CLI command, no "downloaded" history section, no download automation (that's the v3 torrent work). Like 👍/👎, the button needs the server, so a page opened straight off disk can't queue

- [x] **"More" button per discovery-angle group** (user request 2026-07-17, shipped 2026-08-28 in `c8f96f4`): `↓ Show 10 more · <angle>` under each group, fetching the next batch for that seed + angle and appending rows through the same `cand_row` macro, so why/👍/👎/⬇/copy/players are identical to round-1 rows.
  - Only the four Discogs list/search angles get a button (`discover.MORE_SOURCES`): artist, label, label-mate, style. Discogs Recommendations is a fixed scraped carousel and the two Last.fm angles have no deeper page, so Last.fm pagination was dropped rather than faked
  - `discover.more_candidates()` paginates `artist_releases` / `label_releases` with a page cursor (6-page cap per click so one press can't spin), and re-asks `style_recommendations` for `len(shown) + batch + 15` and slices the fresh tail since that source has no cursor. Returns `(cands, next_page, exhausted)`; the button latches disabled on `exhausted`
  - The already-shown (artist, title) pairs are read out of the DOM and posted along, so a click never re-adds a row that's already visible — including rows from an earlier "more" click
  - `/api/more` (`web.py`) resolves the new rows to Spotify + YouTube and renders them via `render.render_more_rows()`, all behind `_RUN_LOCK` so it can't race a running dig into a Discogs rate limit
  - Not done as originally sketched: the first click does *not* reuse round 1's trimmed surplus, it re-queries Discogs deeper every time. Needs the server (a page opened off disk no-ops), and the extra rows are not persisted into the archived HTML

- [ ] **Seen / dismissed persistence**: tiny JSON file recording (artist, title) you've evaluated; re-runs skip them so each run is fresh material
- [x] **Improve Spotify resolution rate** (often only 0–3 of 20): strip catalog/format suffixes from Discogs titles ("- EP", "(Original Mix)", catalog numbers), and fall back to track search when album search fails
  - Cleaner code drafted in `src/tecrawl/spotify.py` (`_clean_artist`, `_clean_title`) — handles asterisk/numeric artist disambiguators, parens like `(Shackleton Mixes)`, trailing `Volum N`/`Part N`, and trailing format suffixes (` EP`, ` LP`) without a dash. **Untested** — needs verification once Spotify access is restored.
- [ ] **Create a new Spotify app and test the resolver fix**
  - The previous app got hit with a 21-hour Retry-After cooldown after a too-fast probe loop, then was deleted. Spotify's dashboard currently blocks creating a replacement (account-level limit). Once that clears, recreate the app, drop new client ID/secret into `.env`, run `tecrawl ~/Downloads/2026.04.19_-_source_tracks.csv` and compare resolved counts against the 25-seed baseline (current avg ~3/20).
  - Note (2026-07-14): everything now degrades gracefully without it. Quick search reads pasted Spotify links from the public embed page, free text falls back to Last.fm, and candidate resolution short-circuits after the first credential rejection. New credentials will transparently re-enable canonical seed matching and per-candidate Spotify links/players.
- [x] **Add Spotify rate-limit safety so we never hit the wall again** (shipped 2026-07-14 alongside quick search)
  - [x] Throttle: 0.5 s min interval between all Spotify requests (~120 req/min, well under punitive-backoff pace)
  - [x] Honor `Retry-After` on 429: sleep + retry once when ≤ 60 s; longer waits park Spotify for that duration and the run degrades to search links
  - [x] Circuit breaker: credential rejection (400/401/403 on the token POST) parks Spotify for 10 min, so a dead app costs one request per run instead of one per candidate
  - [ ] Cache negative results with a shorter TTL (raw empty responses are cached 7d at the HTTP layer already; a dedicated shorter-TTL negative cache is still a possible refinement)
  - [ ] Consider a `--dry-run-spotify` flag for development that skips Spotify resolution entirely (useful for iterating on Discogs/Last.fm logic without touching the quota)

## v2 — MP3 folder support (leftovers)

- [ ] Fall back to AcoustID fingerprinting when both tags and filename parsing fail (stretch)

## v3 — auto-upgrade MP3s to higher bitrate via torrent

- [ ] Scan a local folder of MP3s, read bitrate + tags (artist/title/album) — use `mutagen` for tag reading
- [ ] Flag anything below a chosen threshold (e.g. < 320 kbps, or "anything not FLAC")
- [ ] Query the supplied torrent site's API for a matching release at the desired quality
  - Most music trackers worth using are private (Redacted, Orpheus, etc.) and have JSON APIs + per-user API keys — store in `.env`
  - Match strategy: artist + album first, fall back to artist + title for singles; prefer FLAC > 320 MP3 > V0
- [ ] Send the `.torrent` file (or magnet link) to a local torrent client via RPC
  - Transmission and qBittorrent both expose RPC APIs — pick whichever is already installed
  - Configure a dedicated download directory so the upgrade pipeline can find the result
- [ ] Once download completes: match the new file(s) back to the original MP3(s) by tag, move new file into place, archive (don't delete) the old MP3 to a `replaced/` folder so nothing is lost on a bad match
- [ ] Dry-run mode that reports what *would* be replaced without touching anything — essential for first runs
- [ ] Notes:
  - Only point this at trackers where you have an account and the content is something you're entitled to (e.g. re-downloading higher-quality versions of music you already have). Respect tracker rules — auto-snatching can get accounts banned if it violates ratio or rate limits
  - Rate-limit API calls; cache search results so re-runs don't hammer the tracker

## v3 — monthly new releases digest

- [ ] Schedule (cron / launchd) a monthly run
- [ ] Pull new releases from artists in your seed playlist + labels they're on
- [ ] Filter to releases from the last 30 days
- [ ] Email or Obsidian-note output

## Later — ListenBrainz as a fallback similarity source

- [ ] If Last.fm reliability becomes a problem, add ListenBrainz as an open alternative (free, no key needed for reads)

## Later — Save-to-Spotify-playlist

- [ ] Checkbox in the HTML next to each candidate
- [ ] "Save selected to a new Spotify playlist" button
- [ ] Re-introduces user OAuth + `playlist-modify-private` scope
- [ ] Useful once we trust the rec quality enough that bulk-saving is faster than per-track curation

## Nice-to-have

- [x] Thumbs up/down buttons in the HTML → shipped as 👍/👎 + "why?" per row, appending to `feedback.jsonl` via the local server (record-only by decision — collect data first)
- [ ] Feedback-driven ranking: once `feedback.jsonl` has real data, design downranking/filtering (hide 👎 tracks? downweight 👎-heavy discovery angles?) on evidence
- [ ] Filter by BPM range or Spotify audio features (energy, danceability) — note: Spotify deprecated audio-features for new apps in late 2024, check status
- [ ] Exclude tracks already in your library / playlist

---

## Archive

### v2 — transport bar ✅ shipped 2026-08-28

- [x] Fixed bar at the bottom of the viewport whenever something is playing on YouTube: ⏮ / ⏯ / ⏭ / ⏹, artist + title, queue position, elapsed / duration, and a click-to-seek progress rail. ⏮ / ⏭ call `queuePlay(QUEUE.idx ± 1)`, the same entry point the media keys use, so skipping no longer depends on the keys working
- [x] Play/pause and the media-key handlers share `playCurrent` / `pauseCurrent` / `ytPlaying`, so the buttons and the keys can't drift apart (and the buttons are a way to exercise the key path when the keys misbehave)
- [x] Shows for single-row plays too, with ⏮ / ⏭ disabled since there's no queue. Built in JS (`ensureBar`) rather than in the page templates, so the run page and the search page share one copy
- [x] Progress polls `getCurrentTime` / `getDuration` every 500 ms and feeds `mediaSession.setPositionState`, so the OS media hub gets a real scrubber too; the plain-iframe fallback (no IFrame API) exposes no timing and degrades to controls only
- [x] Queue auto-scroll now yields: it stops following once you scroll away deliberately, and the bar's track name is a click-to-jump back to the playing row

### v2 — media-key play queue ✅ shipped 2026-07-17

- [x] ▶ Play all on run pages + search results: every rec with a YouTube match plays in page order (★ Top picks first, deduped); playing row highlights + scrolls into view; auto-advance on end; dead embeds skipped; manual ▶ click stops the queue
- [x] Media Session API wiring: keyboard media keys (play/pause/next/previous) drive the queue with the tab in the background; near-silent audio loop keeps the page registered as the OS player so the YouTube iframe doesn't swallow next/previous (confirmed working by user)

### v2 — Discogs Recommendations revived ✅ fixed 2026-08-28

The angle had been returning nothing. The filed cause was real but was masking a
second, larger one; both are fixed and the angle is verified live (Jeff Mills —
The Bells now yields 10 `discogs_recommendation` candidates, no warnings).

- [x] **Playwright/Chromium revision mismatch** — 1.61.0 pins Chromium `1228`, the cache only had `1234` (left by an unrelated Node `playwright-core`). `playwright install chromium` fixed it. `playwright` is now pinned `>=1.61,<1.62` in `pyproject.toml`: the browser revision is pinned per Playwright minor, so an unbounded `>=` lets a routine `pip install -U` silently re-open exactly this hole
- [x] **Cloudflare was refusing the scraper outright** — invisible until the browser could launch again. Three independent causes, each A/B'd against discogs.com:
  - Playwright's default headless is the `chromium_headless_shell` build, which **never** passes the challenge (45 s of waiting still shows "Just a moment…"). Now launches `channel="chromium"` — the full Chrome build in new-headless mode
  - The hardcoded `Chrome/124` UA contradicted the browser's real version (149), which still goes out in the `Sec-CH-UA` client hints; the default UA advertises `HeadlessChrome` outright. Now derived from the browser at runtime with only "Headless" stripped, so UA and client hints agree and it stays correct across Chromium upgrades
  - **`page.route("**/*")` interception is itself a bot signal** — the decisive one. Same browser, same UA, routing off clears in ~5 s; routing on never clears. A/B'd three ways: no routing passed, host-only failed, host+resource-type failed. The ad/tracker blocking is deleted with a comment saying not to reintroduce it (its stated purpose, letting `networkidle` fire, was already moot — the code navigates `wait_until="commit"`)
- [x] **A challenge no longer masquerades as "no recommendations"** — it raises `ScrapeUnavailable` instead of returning `[]`. Previously an interstitial was indistinguishable from a rec-less release and got cached as an empty for 6 h, poisoning that release
- [x] **`cf_clearance` persisted** to `.cache/discogs_cf_state.json` and reused, so a run rides on one challenge pass instead of re-solving per release — re-solving back-to-back is what gets an IP clamped down on. Measured: 30 s+ and frequently blocked without it, ~2 s per release with it
- [x] **Circuit breaker** mirroring `spotify.py` — 2 consecutive blocks parks the scraper for 10 min, so a genuinely blocked run doesn't burn ~14 min of a 25-seed dig waiting out challenges
- [x] **Parser fixes** found during verification: `aria-label` HTML entities are now unescaped (`H&amp;M` was reaching search as literal `H&amp;M`), and a cache hit and a fresh scrape now return the same shape (tuples; the fresh path returned lists)
- Note for future debugging: the release page embeds its GraphQL state in `#dsdata`, including `recommendations({"first":10})` with a `totalCount`. That's how "this release has no recommendations" was positively confirmed rather than inferred — most releases legitimately have none (4 of 6 sampled), so an empty result is not evidence of breakage. Parsing that JSON instead of the DOM would be more robust than the `aria-label` regex and would remove the hydration wait entirely; not done, but it's the obvious next move if the carousel markup ever shifts

### v2 — Discogs recommendations scraping ✅ shipped 2026-07-17

Discogs' "Recommendations" carousel (visible on release/master pages) has no
public API — it's a client-hydrated widget, not part of the REST API — and
plain HTTP scraping is blocked by Cloudflare's Managed Challenge.

- [x] **Install Playwright + Chromium** (`pip install playwright && playwright install chromium`) — added as a core dependency; headless Chromium gets past the Managed Challenge cleanly (confirmed live against discogs.com)
- [x] **Build `src/tecrawl/discogs_scrape.py`**: navigates to `https://www.discogs.com/release/<id>`, waits for `#release-recommendations`, and parses each card's `aria-label="Artist - Title"` next to its `/release/<id>-slug` href — avoids depending on the section's build-hashed CSS module class names
- [x] **Cache aggressively** — reuses `cache.py` (now takes an optional `ttl` override), keyed by release URL, 30-day TTL vs. the API's 7-day
- [x] **Throttle and respect ToS** — 4s minimum between scrape navigations, logged-out, no paywalled content
- [x] **Wire into `discover.py`**: called only when `release.release_id` is set, tagged `discogs_recommendation`, `SOURCE_LABELS`/`SOURCE_DESCRIPTIONS` entry ("Recommended on Discogs"), added to `_ALBUM_SOURCES` (Spotify resolution) and `render.py`'s `_SOURCE_ORDER`
- [x] ~~Update template: new tag color for the new source~~ — the `.tag.*` CSS classes turned out to already be dead code (unreferenced by any template, group headers are colored by label text only), so skipped rather than extending unused styling

### Why not cloudscraper (attempted 2026-04-19)
Tested with `cloudscraper.create_scraper(...)` against `discogs.com/release/26378150`. Returned **403 Cf-Mitigated: challenge** with the modern "Enable JavaScript and cookies to continue" page. Cloudflare moved to Turnstile / Managed Challenge which requires real JS execution + Sec-CH-UA-* client hints; cloudscraper still solves the *old* JS challenge but is no longer effective for Discogs. Confirmed in `scratch/probe_cloudscraper.py`.

### v2 — folder input ✅ shipped 2026-07-17

- [x] Accept a folder path as alternate input: `tecrawl <folder>` and pasting an absolute path (or `file://` URL) into the web search box; single audio files run as a quick search seeded from their tags
- [x] Artist/title from tags first via mutagen (ID3, MP4, Vorbis/FLAC, ASF; MP3/M4A/AAC/FLAC/OGG/Opus/WAV/AIFF/WMA/WavPack/APE), recursive scan, hidden files skipped, duplicate (artist, title) collapsed
- [x] Filename parsing as fallback: `Artist - Title`, track-number prefixes (`01 - `, `03. `, vinyl `A1 `), `[Label]` suffixes, `Artist_-_Title` underscores; unreadable files are skipped and listed, never guessed
- [x] Web folder runs stream each seed's results block as it finishes (new `seed_html`/`note`/`done` SSE events); ★ Top picks prepends on completion (shared `_top_picks.html.j2` include, so the playlist page can't drift); closing the tab finishes the current seed, persists what's done, and stops
- [x] Runs persist under `output/<folder name>/` like any playlist run
- [x] 📁 Browse button on index + search: `/api/pick-folder` opens the native macOS folder picker (osascript) and starts the run
- [x] "server started …" footer on index + search — a stale pre-update server is visible at a glance (a stale server free-text-searching a pasted path was the one bug report during review)
- [x] YouTube playback rebuilt on the official IFrame API (`youtube-nocookie` host, playsinline, programmatic play on ready): reliable autoplay, readable failure reasons under the player, plain-iframe fallback when the API script is blocked

### v2 — interactive quick search ✅ shipped 2026-07-14

- [x] `/search` page on the local server: paste a track name, a Spotify track/album link, or a YouTube link; progress streams live (SSE) and the results render inline with the usual players. The index page gets a search box; `tecrawl quick "<track|link>"` is the terminal equivalent
- [x] Every search also persists as a normal output page under `output/Quick searches/` (shared index entry, permalink shown on completion)
- [x] Input resolution (`seed_input.py`): YouTube via oEmbed with title cleaning (PREMIERE:/bracket noise stripped, `(Original Mix)` kept, "Topic" channels exact); Spotify links via API with a no-auth embed-page fallback; free text via Spotify or Last.fm search; helpful errors for playlist/artist/other links
- [x] Seed itself is playable: Spotify button when resolvable plus a YouTube button (the exact video when the input was a YouTube link)
- [x] Server rewrite (`web.py`): threaded (long runs don't block page loads), one-discovery-at-a-time lock, `--no-open` / `--local` flags; playlist page and quick search share one `_seed_block` template so they can't drift

- [x] **Cross-seed scoring**: candidates surfaced from 3+ seeds bubble to a "★ Top picks" section at the top of the HTML, sorted by hit count, with a collapsible per-seed dropdown that includes a play button to audition each originating seed

### v1 — minimum viable digger ✅ shipped 2026-04-19

- [x] Spotify auth (client credentials for /search; user OAuth not needed since input pivoted to CSV)
- [x] ~~Read tracks from a public Spotify playlist URL~~ — **pivoted to CSV input** (Exportify)
  - Spotify Web API blocks `/playlists/{id}/tracks` for new (Developer Mode) apps; confirmed with 403s on three of the user's own public playlists. Workaround: export via https://watsonbox.github.io/exportify/ → CSV → tecrawl
- [x] Discogs auth (personal access token)
- [x] Last.fm auth (API key, read-only)
- [x] **Discogs discovery angles** per seed:
  - Search Discogs → resolve to a release → get label(s) and artist(s)
  - 1. Other releases on the **same label**
  - 2. Other releases by the **same artist**
  - 3. Releases by **other artists who appear on that label**
- [x] **Last.fm discovery angles** per seed:
  - 4. `track.getSimilar`
  - 5. `artist.getSimilar` → top tracks for each similar artist
- [x] Merge to ~20 candidates per seed; dedupe across seeds; tag each with its source angle (1–5)
- [x] Resolve each candidate back to a Spotify track/album ID via Spotify search
  - Discogs candidates → `/search?type=album` (release titles aren't track titles)
  - Last.fm candidates → `/search?type=track`
  - **Artist-name sanity check** (NFD-normalized substring match) so wildly wrong matches no longer slip through
- [x] Generate static HTML output grouped by seed track
  - Each candidate row: track + artist, "why" tag, Spotify link, YouTube fallback
  - One section per seed
  - **Inline Spotify IFrame-API player** (one-click ▶ → loads + plays; only one active at a time)
- [x] Single CLI command: `tecrawl <csv-path>` writes to `output/<timestamp>.html`
- [x] Respect Discogs rate limits (60 req/min) — file-based JSON cache (7d TTL), 1.05 sec throttle
- [x] Respect Last.fm rate limits (5 req/sec) — same cache, 0.25 sec throttle
