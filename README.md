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

1. Export the playlist you want recommendations from at https://watsonbox.github.io/exportify/ (authorize with Spotify, pick the playlist, click Export → downloads a CSV).
2. Run:

```bash
tecrawl path/to/playlist.csv

# or test with a small subset first:
tecrawl path/to/playlist.csv --max-seeds 3
```

Output lands at `output/<timestamp>.html` — open it in a browser. API responses are cached under `.cache/` for 7 days, so re-runs are fast and don't burn rate limits.

If you pass a Spotify playlist URL by mistake, the CLI prints a helpful error pointing to Exportify.
