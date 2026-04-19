# teCrawl

Automated techno discovery tool. Feed it tracks you like, get back a web page of similar tracks with one-click Spotify links.

## How it works

1. **Seed**: a Spotify playlist of tracks you like (v1) or a folder of MP3s (v2).
2. **Discover**: for each seed track, pull candidates from two complementary sources:
   - **Discogs** — same label, same artist, other artists on that label (the "adjacent in the catalog" finds)
   - **Last.fm** — `track.getSimilar` and `artist.getSimilar` (the "people who scrobbled this also scrobbled" finds)
3. **Resolve**: look each candidate up in Spotify so the output links open directly in the Spotify app.
4. **Render**: generate a static HTML page grouped by seed track, tagging each candidate with which discovery angle surfaced it.

## Why this stack

- **Discogs + Last.fm together**: they overlap a little but mostly find different things. Discogs gives you label/catalog adjacency (which matters in techno more than most genres). Last.fm gives you scrobble-based "sounds similar" recommendations.
- **Spotify** for input + output: easiest to curate seeds (just add to a playlist on your phone) and gives clean deep links for the output page.
- **Static HTML output**: no server, no hosting, just open the file. Easy to archive past runs.

See `TODO.md` for what's planned beyond v1 (MP3 ingest, Discogs recommendations scraping, monthly new-releases digest, auto-upgrading MP3s to higher bitrate, ListenBrainz as a fallback).

## Setup

### 1. API keys

Copy `.env.example` to `.env` and fill in:

- `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` — from https://developer.spotify.com/dashboard
- `DISCOGS_TOKEN` — personal access token from https://www.discogs.com/settings/developers (click "Generate new token")
- `LASTFM_API_KEY` — from https://www.last.fm/api/account/create (only the API key, not the shared secret)

### 2. Install

_TBD — depends on language choice (Python recommended)._

## Usage

_TBD — will be a single command that takes a Spotify playlist URL and writes an HTML file to `output/`._

```
tecrawl <spotify-playlist-url>
open output/<timestamp>.html
```
