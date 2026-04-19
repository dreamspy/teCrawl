# TODO

## v1 — minimum viable digger ✅ shipped 2026-04-19

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

## v2 — high-leverage next steps

- [ ] **Cross-seed scoring**: when a candidate appears from multiple seeds, that's much higher signal than a one-off — surface those at the top in a "Top picks" section
- [ ] **Seen / dismissed persistence**: tiny JSON file recording (artist, title) you've evaluated; re-runs skip them so each run is fresh material
- [ ] **Improve Spotify resolution rate** (often only 0–3 of 20): strip catalog/format suffixes from Discogs titles ("- EP", "(Original Mix)", catalog numbers), and fall back to track search when album search fails

## v2 — Discogs recommendations scraping (next)

- [ ] Scrape the "Recommendations" section from Discogs release pages (no public API for this)
- [ ] Respect rate limits and ToS — cache aggressively
- [ ] Add as a sixth discovery angle, tagged `discogs_recommendation` in the HTML output

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

- [ ] Persist a "seen" / "dismissed" file so the same recs don't keep reappearing across runs
- [ ] Thumbs up/down buttons in the HTML that write to that file (would need a tiny local server, or a `<a>`-link hack)
- [ ] Score/rank candidates by how many seeds they were surfaced from + which discovery angle (cross-seed frequency = high signal)
- [ ] Filter by BPM range or Spotify audio features (energy, danceability) — note: Spotify deprecated audio-features for new apps in late 2024, check status
- [ ] Exclude tracks already in your library / playlist
