# TODO

## v2 — high-leverage next steps

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

## v2 — Discogs recommendations scraping (next, via Playwright)

Goal: scrape the "Recommendations" section from Discogs release pages and add
as a 6th discovery angle, tagged `discogs_recommendation` in the HTML output.
There's no public API for this — the data only exists in the rendered page.

- [ ] **Install Playwright + Chromium** (`pip install playwright && playwright install chromium`) — adds ~200MB but it's the only path that survives Cloudflare's current Managed Challenge
- [ ] **Build `src/tecrawl/discogs_scrape.py`**: launch headless Chromium, navigate to `https://www.discogs.com/release/<id>`, wait for the recommendations section to render, parse out artist + title pairs (and ideally release IDs)
- [ ] **Cache aggressively** — re-use the existing JSON cache pattern, key by release_id, longer TTL than the API cache (recommendations don't change daily)
- [ ] **Throttle and respect ToS** — keep page navigations slow (e.g. 3–5 sec between requests) and run as logged-out (no scraping behind paywalls)
- [ ] **Wire into `discover.py`**: only call when we have a seed `release.release_id`; add candidates with `source="discogs_recommendation"` and a new SOURCE_LABELS entry ("Recommended on Discogs")
- [ ] **Update template**: new tag color for the new source

### Why not cloudscraper (attempted 2026-04-19)
Tested with `cloudscraper.create_scraper(...)` against `discogs.com/release/26378150`. Returned **403 Cf-Mitigated: challenge** with the modern "Enable JavaScript and cookies to continue" page. Cloudflare moved to Turnstile / Managed Challenge which requires real JS execution + Sec-CH-UA-* client hints; cloudscraper still solves the *old* JS challenge but is no longer effective for Discogs. Confirmed in `scratch/probe_cloudscraper.py`.

## v2 — MP3 folder support

- [ ] Accept a folder path as alternate input
- [ ] Parse `Artist - Title` from filenames (handle common patterns: `01 - Artist - Title.mp3`, `Artist - Title (Remix) [Label].mp3`)
- [ ] Feed parsed (artist, title) into the same Discogs pipeline
- [ ] Fall back to AcoustID fingerprinting if filename parsing fails (stretch)

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

- [ ] Thumbs up/down buttons in the HTML that write to a seen/dismissed file (would need a tiny local server, or a `<a>`-link hack)
- [ ] Filter by BPM range or Spotify audio features (energy, danceability) — note: Spotify deprecated audio-features for new apps in late 2024, check status
- [ ] Exclude tracks already in your library / playlist

---

## Archive

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
