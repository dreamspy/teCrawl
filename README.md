# teCrawl

Automated techno discovery tool. Feed it tracks you like, get back a web page of similar tracks with one-click Spotify links.

Three ways in: a whole playlist (CSV export, see Usage), a local folder of audio files (MP3/M4A/FLAC/…), or a single track via the interactive quick search.

## Quick search: one track in, recommendations out

The fastest way to use teCrawl. Start the server (`tecrawl serve` or `./open-output.sh`), open **http://localhost:8765/search** (the index page has the same search box), and paste any of:

- a track name: `Blawan - Getatchew` (plain free text works too)
- a Spotify track or album link: `https://open.spotify.com/track/…` (album links seed from the album's first track)
- a YouTube link: `https://youtu.be/…`, `youtube.com/watch?v=…`, YouTube Music, or Shorts
- a local folder of audio files: `/Users/you/Music/crate` (multi-seed — each track's results stream in as they finish, see below)
- a single local audio file: `/Users/you/Music/track.mp3` (seeds a normal quick search from its tags)

Progress streams live while Discogs and Last.fm are crawled (typically 30–90 s on a fresh seed, instant when cached), then the results appear inline with the usual inline players. Every search is also archived as a page under **Quick searches** on the index, with a permalink shown when it finishes.

Terminal equivalent:

```bash
tecrawl quick "Blawan - Getatchew"      # also takes Spotify/YouTube links
```

Input-handling notes:

- YouTube video titles are cleaned before matching: `PREMIERE:` prefixes, `(Official Video)`-style brackets, and trailing `[Label]` tags are stripped, while remix info like `(Original Mix)` survives. Auto-generated "Topic" channel videos resolve exactly from their metadata.
- The parsed seed is verified against Spotify when possible, which also makes the seed itself playable.
- Everything still works while the Spotify app credentials are dead: pasted Spotify links are read from Spotify's public embed page (no auth), free-text search falls back to Last.fm, and candidates keep their YouTube players plus Spotify search links.

## View your latest output

```bash
./open-output.sh
```

That's it — the script activates the venv, starts the local server if it isn't already running, and opens http://localhost:8765/ in your browser. If the server is already up, it just reopens the URL (no double-start).

From there, `/` lists every playlist you've run (plus the quick-search box) and `/<playlist-name>/` always serves the most recent output for that playlist — bookmark whichever URL you use most. Leave the server running; after each new `tecrawl` run, just refresh.

**Phone access:** the server binds to all interfaces, so the URL is also reachable over [Tailscale or LAN](#viewing-from-your-phone). Always view via the server — YouTube embeds refuse `file://` origins and won't play.

## How it works

1. **Seed**: a CSV of tracks (export your Spotify playlist via [Exportify](https://watsonbox.github.io/exportify/) — one-time browser auth, takes ~10 seconds per playlist), or a local folder of audio files (artist/title read from tags, filename parsing as fallback).
2. **Discover**: for each seed, pull candidates from two complementary sources:
   - **Discogs** — same label, same artist, other artists on that label, and Discogs' own "Recommendations" carousel scraped from the release page (the "adjacent in the catalog" finds, resolved as Spotify *album* links)
   - **Last.fm** — `track.getSimilar` and `artist.getSimilar` (the "people who scrobbled this also scrobbled" finds, resolved as Spotify *track* links)
3. **Resolve**: look each candidate up in Spotify with an artist-name sanity check so we don't link the wrong thing.
4. **Render**: generate a static HTML page grouped by seed, with inline Spotify previews (one-click ▶ to play) and YouTube fallback links.

## Why this stack

- **Discogs + Last.fm together**: they overlap a little but mostly find different things. Discogs gives you label/catalog adjacency (which matters in techno more than most genres). Last.fm gives you scrobble-based "sounds similar" recommendations.
- **CSV via Exportify** for input: Spotify's Web API blocks new (Developer Mode) apps from reading playlist tracks directly, so we use a one-time export. Folder input (shipped) feeds the same pipeline from local files' tags.
- **Spotify for output**: clean deep links + an inline IFrame-API player so you can audition without leaving the page.
- **Static HTML output**: no server, no hosting, just open the file. Easy to archive past runs.

See `TODO.md` for what's planned next (Discogs recommendations scraping, monthly new-releases digest, auto-upgrading MP3s to higher bitrate, ListenBrainz as a fallback).

## Setup

### 1. API keys

Copy `.env.example` to `.env` and fill in:

- `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` — from https://developer.spotify.com/dashboard
- `DISCOGS_TOKEN` — personal access token from https://www.discogs.com/settings/developers (click "Generate new token")
- `LASTFM_API_KEY` — from https://www.last.fm/api/account/create (only the API key, not the shared secret)

### 2. Install (Python 3.11+)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium   # one-time, ~200MB — needed for Discogs' Recommendations carousel (no public API for it)
```

## Usage

The full workflow is **Spotify playlist → Exportify CSV → tecrawl → HTML page**.

### Step 1: export your Spotify playlist to CSV

1. Open the playlist in Spotify (web or desktop) and copy its URL — looks like `https://open.spotify.com/playlist/3q6dviV9Kf...`.
2. Go to **https://watsonbox.github.io/exportify/** in your browser.
3. Click **Sign in with Spotify** and authorize Exportify (read-only access). One-time per browser.
4. Find your playlist in the list and click **Export**. A CSV file downloads, e.g. `My Techno Picks.csv`.
5. Drop it into the `input/` folder at the repo root (gitignored — personal CSVs don't get committed). The CSV filename becomes the playlist name and the output folder name, so rename it to whatever you want the run labelled as before running.

> **Why CSV?** Spotify's Web API blocks new (Developer Mode) apps from reading playlist tracks directly, so we route through Exportify. Takes ~10 seconds per playlist.

### Step 2: run tecrawl

```bash
# activate the venv if you haven't already
source .venv/bin/activate

# full run (every track in the CSV)
tecrawl input/My-Techno-Picks.csv

# quick smoke test on the first 3 tracks
tecrawl input/My-Techno-Picks.csv --max-seeds 3
```

The tool prints progress per seed (`[12/25] Artist — Title → 20 candidates, 7 on Spotify, 18 on YouTube`) and writes the result to `output/<timestamp>.html`.

### Alternative: run on a folder of audio files

Point tecrawl at a folder instead of a CSV and every audio file in it (recursively) becomes a seed:

```bash
tecrawl ~/Music/crate                  # folder name becomes the playlist name
tecrawl ~/Music/crate --max-seeds 3    # smoke test
```

Or paste the folder path straight into the web search box — each seed's results stream onto the page as they finish, and the run is archived under the folder's name like any playlist run.

- **Formats**: MP3, M4A/AAC, FLAC, OGG/Opus, WAV, AIFF, WMA, WavPack, APE.
- **Artist/title come from tags first** (ID3, MP4, Vorbis, …). Untagged files fall back to filename parsing: `Artist - Title.mp3`, with track-number prefixes (`01 - `, `03. `, vinyl `A1 `), `[Label]` suffixes, and `Artist_-_Title` underscores handled.
- Files with neither usable tags nor a parseable filename are **skipped and listed** — never guessed at. Duplicate (artist, title) pairs collapse into one seed.
- A single audio file works too: `tecrawl ~/Music/track.mp3` runs a quick search seeded from its tags.

### Step 3: open the result

```bash
tecrawl serve
```

Starts a tiny local HTTP server (default port 8765) and opens it in your browser. **Use this rather than opening the file directly** — YouTube embeds refuse `file://` origins and won't play. The server runs until you Ctrl-C.

Routing:

- `/` — index page: quick-search box plus every playlist you've run.
- `/search` — interactive quick search (single track/link in, recommendations out).
- `/queue` — your ⬇ download queue: everything you've marked "grab this later".
- `/<playlist-slug>/` — opens the most recent run for that playlist. Refresh after each new `tecrawl` call to see the updated page.
- `/<playlist-slug>/<timestamp>.html` — a specific historical run.

Flags: `tecrawl serve [port] [--no-open] [--local]`. `--no-open` skips auto-opening the browser; `--local` binds to 127.0.0.1 only (the default binds all interfaces so phones can reach it, see below).

Output files are grouped on disk as `output/<playlist-slug>/<timestamp>.html`, so different playlists stay separate and you can keep multiple side by side.

### Viewing from your phone

The server binds to all interfaces, so it's reachable beyond the Mac:

- **Tailscale** (recommended, works anywhere) — if `tailscale` is installed and logged in, `tecrawl serve` prints a line like `Tailscale: http://<machine>.<tailnet>.ts.net:8765/`. Open that on your phone (phone must also be on the same tailnet).
- **Same WiFi** — browse to `http://<mac-lan-ip>:8765/` from the phone. Find the Mac's IP with `ipconfig getifaddr en0` (or System Settings → Network).

Bookmark either URL on the phone. After each `tecrawl <csv>` run, pull to refresh — the index updates and the per-playlist URL always serves the latest.

Each row has:
- **▶ green** play button (Spotify inline preview, when the candidate exists on Spotify)
- **▶ red** play button (YouTube inline embed — works for nearly everything)
- **Spotify** / **Search** link (deep link to Spotify or a search if not resolved)
- **why?** — expands a one-line explanation of exactly why this track surfaced (which discovery angle, from which of your seeds)
- **👍 / 👎** — record your verdict; click the same thumb again to undo. After a thumb, an optional note field opens (quick-tap chips like "totally irrelevant" / "more like this", or free text) — your own reason is the highest-signal data for tuning the algorithm later
- **⬇** — add to the download queue (see below); click again to remove

The **★ Top picks** section at the top shows candidates that surfaced from 3+ of your seed tracks — strong cross-signal.

### ▶ Play all + media keys

The **▶ Play all** button (top of every run page, and on search results) plays every recommendation on the page through its YouTube player, in page order (★ Top picks first), skipping duplicates and anything without a YouTube match. Your keyboard's **media keys** control the queue — play/pause, next track, previous track — even with the tab in the background, via the browser's Media Session API. The currently playing row is highlighted and scrolled into view; dead embeds are skipped automatically; clicking any ▶ manually stops the queue and takes over.

Implementation note: a near-silent looping audio element keeps the *page* registered as the OS media player (otherwise the YouTube iframe grabs the media keys and next/previous wouldn't work). If media keys don't respond, click once anywhere on the page first, and check the browser's media hub (the ♪ icon in Chrome's toolbar) shows "teCrawl".

### Feedback (record-only for now)

Every 👍/👎 appends one line to `feedback.jsonl` at the repo root (gitignored — personal taste data): track, verdict, discovery angle, originating seed, page, timestamp. Verdicts survive reloads and show up on every page, including old archived runs (state is applied from the server, so pages generated before a verdict still display it). **Deliberately no ranking effects yet**: the plan is to collect real data first, then design downranking/filtering on evidence. Requires viewing through `tecrawl serve` (the buttons talk to the local server).

### ⬇ Download queue

Hitting **⬇** on any row (candidate, ★ top pick, or the seed header) marks it "grab this later" without interrupting what you're listening to. Click it again to take it back out.

Everything queued is listed at **`/queue`**, linked from the top of every page and with a count on the index. That page shows each track with the seed you were digging when you queued it, the usual YT / Last.fm / Discogs / Dig links, a working YouTube player and ▶ Play all, a **×** to remove an entry by hand, and a **copy** button on each row for that one `Artist - Title`, and a **Copy list** button that puts every queued track on your clipboard one per line — paste either into a store search, a tracker, or wherever you actually acquire music.

Storage is `download_queue.jsonl` at the repo root (gitignored, same as `feedback.jsonl`): append-only, so the full history of what you queued and un-queued is preserved even though `/queue` only shows what's currently waiting. **Not a Spotify playlist** — that would need OAuth this project doesn't have and would only ever hold candidates that resolved to Spotify, missing every YouTube-only find. Requires viewing through `tecrawl serve` (the button talks to the local server).

### Re-running

API responses are cached under `.cache/` for 7 days, so re-running with the same CSV is fast and won't burn rate limits. Delete `.cache/` to force a fresh fetch.

If you pass a Spotify playlist URL by mistake, the CLI prints a helpful error pointing to Exportify.

## Browser notes

### Safari: enable autoplay for localhost

Safari blocks audio autoplay by default even on user click, so the inline Spotify player won't start automatically until you allow it once:

1. Open `http://localhost:8765/` in Safari (so the site appears in the autoplay list).
2. Safari menu → **Settings** → **Websites** tab → **Auto-Play** in the left sidebar.
3. Find **localhost** in the list (or set the bottom dropdown "When visiting other websites" to **Allow All Auto-Play**).
4. Set localhost to **Allow All Auto-Play**.
5. Refresh the page.

Firefox and Chrome honor the user-click gesture by default — no settings change needed.

### YouTube embeds need an HTTP origin

YouTube's iframe player refuses `file://` origins (you'll see "Error 153 — Video player configuration error"). Always view the HTML via `tecrawl serve`, not by double-clicking the file.

### YouTube: autoplay and "An error occurred"

The red ▶ buttons drive the official YouTube IFrame API (privacy-enhanced `youtube-nocookie.com` host). When an embed can't play, teCrawl now prints the reason under the player (bad id, video removed, embedding disabled). If you instead get YouTube's generic **"An error occurred. Please try again later. (Playback ID: …)"** on many different videos, the cause is almost always in the browser, not the page:

- **Ad blocker / content blocker** (uBlock, AdGuard, Brave shields, Pi-hole): they let the thumbnail through but block the actual `googlevideo.com` stream. Allowlist `localhost` (or your Tailscale hostname) in the blocker.
- **Safari autoplay settings**: same as the Spotify note above, and the allow entry must match the hostname you're actually browsing from — allowing `localhost` does nothing when you open the Tailscale URL, and vice versa.
