# teCrawl

Automated techno discovery tool. Feed it tracks you like, get back a web page of similar tracks with one-click Spotify links.

## How it works

1. **Seed**: a CSV of tracks (export your Spotify playlist via [Exportify](https://watsonbox.github.io/exportify/) — one-time browser auth, takes ~10 seconds per playlist).
2. **Discover**: for each seed, pull candidates from two complementary sources:
   - **Discogs** — same label, same artist, other artists on that label (the "adjacent in the catalog" finds, resolved as Spotify *album* links)
   - **Last.fm** — `track.getSimilar` and `artist.getSimilar` (the "people who scrobbled this also scrobbled" finds, resolved as Spotify *track* links)
3. **Resolve**: look each candidate up in Spotify with an artist-name sanity check so we don't link the wrong thing.
4. **Render**: generate a static HTML page grouped by seed, with inline Spotify previews (one-click ▶ to play) and YouTube fallback links.

## Why this stack

- **Discogs + Last.fm together**: they overlap a little but mostly find different things. Discogs gives you label/catalog adjacency (which matters in techno more than most genres). Last.fm gives you scrobble-based "sounds similar" recommendations.
- **CSV via Exportify** for input: Spotify's Web API blocks new (Developer Mode) apps from reading playlist tracks directly, so we use a one-time export. CSV input also unifies with the planned MP3-folder workflow (v2).
- **Spotify for output**: clean deep links + an inline IFrame-API player so you can audition without leaving the page.
- **Static HTML output**: no server, no hosting, just open the file. Easy to archive past runs.

See `TODO.md` for what's planned beyond v1 (MP3 ingest, Discogs recommendations scraping, monthly new-releases digest, auto-upgrading MP3s to higher bitrate, ListenBrainz as a fallback).

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
```

## Usage

The full workflow is **Spotify playlist → Exportify CSV → tecrawl → HTML page**.

### Step 1: export your Spotify playlist to CSV

1. Open the playlist in Spotify (web or desktop) and copy its URL — looks like `https://open.spotify.com/playlist/3q6dviV9Kf...`.
2. Go to **https://watsonbox.github.io/exportify/** in your browser.
3. Click **Sign in with Spotify** and authorize Exportify (read-only access). One-time per browser.
4. Find your playlist in the list and click **Export**. A CSV file downloads, e.g. `My Techno Picks.csv`.

> **Why CSV?** Spotify's Web API blocks new (Developer Mode) apps from reading playlist tracks directly, so we route through Exportify. Takes ~10 seconds per playlist.

### Step 2: run tecrawl

```bash
# activate the venv if you haven't already
source .venv/bin/activate

# full run (every track in the CSV)
tecrawl ~/Downloads/My\ Techno\ Picks.csv

# quick smoke test on the first 3 tracks
tecrawl ~/Downloads/My\ Techno\ Picks.csv --max-seeds 3
```

The tool prints progress per seed (`[12/25] Artist — Title → 20 candidates, 7 on Spotify, 18 on YouTube`) and writes the result to `output/<timestamp>.html`.

### Step 3: open the result

```bash
tecrawl serve
```

Starts a tiny local HTTP server (default port 8765) and auto-opens the most recent HTML in your browser at `http://localhost:8765/...`. **Use this rather than opening the file directly** — YouTube embeds refuse `file://` origins and won't play. The server runs until you Ctrl-C.

Each row has:
- **▶ green** play button (Spotify inline preview, when the candidate exists on Spotify)
- **▶ red** play button (YouTube inline embed — works for nearly everything)
- **Spotify** / **Search** link (deep link to Spotify or a search if not resolved)

The **★ Top picks** section at the top shows candidates that surfaced from 3+ of your seed tracks — strong cross-signal.

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
