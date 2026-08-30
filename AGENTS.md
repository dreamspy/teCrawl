# teCrawl

Personal techno discovery tool. Reads a Spotify playlist (or MP3 folder, v2) and outputs a static HTML page of similar tracks pulled from Discogs (label/artist graph) and Last.fm (scrobble similarity), with one-click Spotify links.

See `README.md` for the user-facing overview and `TODO.md` for the roadmap.

## Rules

### Never commit secrets

**This is a public GitHub repo (`dreamspy/teCrawl`).** API keys, OAuth secrets, access tokens, passwords, and any other credentials must never reach git history.

- Real credentials live in `.env`, which is gitignored. Only `.env.example` (with empty values) is tracked.
- Before every `git add` / `git commit`, scan the diff for credential-shaped strings — long random tokens, things named `*_KEY`, `*_SECRET`, `*_TOKEN`, `password`, `auth`, etc.
- **Never use `git add -A` or `git add .`** — always stage specific files by name. This is the single most common way secrets leak.
- If a secret is ever committed (even locally, even before pushing): treat it as compromised. Rotate it at the source service immediately, then rewrite history with `git filter-repo` or BFG. Removing the file in a later commit does not undo the leak.
- Watch list of files that should *never* be staged: `.env`, `.env.local`, `*.key`, `*.pem`, `credentials*.json`, `secrets*.json`, `service-account*.json`, anything in `~/.aws/`, `~/.ssh/`, or `~/.config/`.

## Architecture

Orientation for someone touching the code. `README.md` is the user-facing
manual (setup, flags, browser quirks) and `TODO.md` is the roadmap plus a
dated archive of what shipped and what was already tried; this section is
only the shape of the system, and points at those two rather than repeating
them.

### Mental model

One seed track in, a page of adjacent tracks out. Every path through the tool
is the same four stages: **seed** (a CSV row, a tagged audio file, a pasted
link, or typed text becomes a `spotify.Track`) → **discover** (Discogs and
Last.fm are queried along six independent "angles" and each result becomes a
`discover.Candidate`) → **resolve** (each candidate is looked up on Spotify,
then on YouTube, so the row has something playable) → **render** (Jinja
templates write a self-contained HTML page under `output/`). The CLI does this
in a loop over the seeds and writes the page at the end. `tecrawl serve` runs
the *same functions* but streams progress and per-seed result HTML to the
browser over Server-Sent Events, and still persists the same page to
`output/` when it finishes, so a live dig and a CLI dig produce identical
artifacts. There is no database and no application state beyond files on disk.

### Entry points

| Command | Code path |
| --- | --- |
| `tecrawl <csv>` / `tecrawl <folder>` | `cli.main` → `seeds.from_csv` or `localfiles.scan` → loop of `discover.*` → `render.render` |
| `tecrawl <one-audio-file>` | `cli.main` → `localfiles.seed_from_file` → falls through to `quick.run` |
| `tecrawl quick "<track|link>"` | `cli._run_quick` → `quick.run` |
| `tecrawl serve [port] [--no-open] [--local]` | `cli._run_serve` → `web.serve` |
| `./open-output.sh` | starts `tecrawl serve` unless something already listens on the port |

`cli.main` hand-dispatches `serve` and `quick` *before* argparse runs, so the
bare positional (`tecrawl input/foo.csv`) keeps working. Adding a subcommand
means adding another branch there, not an argparse subparser.

### Module map (`src/tecrawl/`)

Seeding:
- `seeds.py` — Exportify CSV → `[spotify.Track]`. Tolerant column matching.
- `localfiles.py` — folder/file → seeds. Tags via mutagen first, `Artist -
  Title` filename parsing as fallback, unparseable files reported as skipped
  rather than guessed. No network.
- `seed_input.py` — one pasted string → one seed. Spotify/YouTube links,
  `Artist - Title`, or free text; cleans YouTube video-title noise and
  canonicalises against Spotify when it can.

Discovery and resolution:
- `discover.py` — the core. `discover_for_seed()` runs every angle,
  dedupes, and balances them into a fixed slot budget; `resolve_to_spotify()`
  / `resolve_to_youtube()` fill in playable ids; `aggregate_top_picks()` finds
  cross-seed repeats; `more_candidates()` backs the "Show more" button. Owns
  `SOURCE_LABELS` / `SOURCE_DESCRIPTIONS` (the user-facing names of the angles).
- `discogs.py` — Discogs REST API: anchor-release search, label/artist release
  lists, style-based search. Token auth, 1.05 s throttle.
- `discogs_scrape.py` — the one non-API source: the Recommendations carousel,
  scraped from the release page with Playwright through Cloudflare.
- `lastfm.py` — `track.getsimilar`, `artist.getsimilar`, `artist.gettoptracks`,
  plus a free-text `track.search` used as a resolver fallback.
- `youtube.py` — keyless: video id from the public search page (regex over the
  embedded JSON), title/channel from the oEmbed endpoint.
- `spotify.py` — **client-credentials** Spotify: catalog search and track/album
  lookup only. Includes the Discogs-noise query cleaners and a no-auth
  fallback that reads metadata out of the public embed page.
- `spotify_connect.py` — **user-OAuth** Spotify: authorization-code flow and
  playback control (play/pause/next/prev/devices) against the user's own
  Spotify client.

Orchestration and output:
- `quick.py` — single-seed pipeline shared by `tecrawl quick` and `/search`
  (not in the original module list but load-bearing).
- `web.py` — the local HTTP server: routing, the SSE discovery endpoint, the
  JSON APIs, the OAuth round trip, and the folder-run multi-seed loop.
- `render.py` — Jinja environment, URL-building globals registered for the
  templates, whole-page `render()` plus the fragment renderers the server
  injects.
- `templates/` — see below.
- `cache.py`, `config.py` — HTTP response cache and `.env`/path config.

Local personal stores:
- `feedback.py` — append-only 👍/👎 log.
- `dlqueue.py` — append-only "grab this later" queue. Same shape as
  `feedback.py` on purpose; that pairing is the house pattern for any new
  local store.

### The discovery angles

`Candidate.source` is the key that ties everything together: it selects the
section label and caption in the UI, decides album-vs-track Spotify
resolution, and gates the "Show more" button. Six angles actually produce
candidates; `discogs_label` is a seventh source key that exists only for
display ordering and "Show more" pagination.

| `source` | Where it comes from |
| --- | --- |
| `discogs_artist` | `artist_releases()`, plus label releases credited to the seed artist |
| `discogs_label_mate` | `label_releases()` entries by *other* artists |
| `discogs_style` | `style_recommendations()` — per-style searches intersected by rank |
| `discogs_recommendation` | `discogs_scrape.recommendations()` |
| `lastfm_track` | `track.getsimilar` |
| `lastfm_artist` | top tracks of `artist.getsimilar` |
| `discogs_label` | **display/pagination only** — `discover_for_seed` never emits it |

Non-obvious consequences:

- Every `discogs_*` source resolves to Spotify **albums**, the two `lastfm_*`
  ones to **tracks** (`discover._ALBUM_SOURCES`). That is what sets
  `Candidate.spotify_type`, which in turn decides whether playback sends a
  `context_uri` or a `uris` list.
- Everything hangs off one anchor release. `discogs.search_release()` falls
  back to an artist-level anchor with `exact=False` when the exact track can't
  be found; without that fallback every Discogs angle dies and the page quietly
  shows only Last.fm. The templates surface both the inexact anchor and
  any angle that came back empty, so "nothing found" is visible rather than
  silent.
- Per-seed output is capped at `CANDIDATES_PER_SEED` (20), round-robin
  balanced across angles — *except* Discogs Recommendations, which is appended
  on top of the budget and is deliberately allowed to repeat rows shown by
  other angles.
- Multi-seed runs thread a shared `run_seen` set through `discover_for_seed`
  so seeds with overlapping styles rotate through different "Same vibe"
  results instead of producing fake cross-seed agreement.
- Top picks require `default_min_hits()` distinct seeds: 3, dropping to 2 for
  runs with fewer than 3 seeds.

### State on disk

Everything durable is a plain file at the project root. All of it except the
code and `.env.example` is gitignored personal data.

| Path | What | Tracked? |
| --- | --- | --- |
| `.env` | API credentials, read by `config.py` | no — see the secrets rules above |
| `.cache/` | HTTP response cache, one JSON file per request | no |
| `.cache/discogs_cf_state.json` | Playwright storage state holding the `cf_clearance` cookie | no |
| `output/<name>/<YYYYMMDD-HHMMSS>.html` | rendered runs; also the server's static root | no |
| `input/` | personal playlist CSVs | no |
| `feedback.jsonl` | append-only 👍/👎 log | no |
| `download_queue.jsonl` | append-only download queue | no |
| `.spotify_token.json` | user OAuth refresh token, chmod 0600 | no — grants playback control of the account |

The output folder name is the display name with unsafe characters replaced
(`render._folder_name`), not a slug: case and spaces are preserved, and re-runs
of the same playlist land in the same folder. Quick searches all pool into
`output/Quick searches/`. Both JSONL stores are read by folding the file
last-write-wins (`feedback.latest()`, `dlqueue.active()`); nothing is ever
rewritten, so deleting an entry means appending a `clear`/`remove` line.

### Caching and rate limits

`cache.py` is a single flat directory keyed by `sha256(url + params)`, shared
by every module, default TTL 7 days. Two things about it are load-bearing:

- **The TTL is a read-time argument, not a property of the entry.** That's how
  `discogs_scrape` gets a 30-day cache for real results but effectively 6 hours
  for empty ones: it calls `cache.get()` twice with different TTLs and only
  trusts an empty result if the short-TTL read also hits.
- **`cache.set_bypass()` is a module global** implementing the "fresh dig"
  checkbox. It only affects reads — writes still repopulate the cache. `web.py`
  toggles it exclusively while holding `_RUN_LOCK`, which is the only reason a
  process-global flag is safe here.

Each network module keeps its own module-level `_LAST_REQUEST` throttle
(Discogs API 1.05 s, Discogs scrape 4 s, Spotify 0.5 s, Last.fm 0.25 s,
YouTube 0.3 s). These are process-global and not thread-safe, which is the
real reason for `_RUN_LOCK`.

Failure handling is "park, don't hammer": `spotify.py` parks the whole API for
10 minutes after a credential rejection and for the `Retry-After` window on a
long 429, raising `SpotifyUnavailable`; `discover.resolve_to_spotify` then
stops trying for the rest of the batch and every remaining row degrades to a
search link. `discogs_scrape` parks for 10 minutes after two Cloudflare blocks.
Dead Spotify credentials are a supported state, not a broken one — the whole
UI is built to work without them.

### The web server (`web.py`)

`ThreadingHTTPServer` subclassing `SimpleHTTPRequestHandler` with
`directory=output/`, so any path that doesn't match an explicit route is served
as a file from `output/`. Routes are checked in order, which is why `/queue` is
handled before the `/<folder>/` fallback.

- GET: `/`, `/search`, `/queue`, `/api/discover` (SSE), `/api/pick-folder`,
  `/api/feedback`, `/api/queue`, `/api/spotify/status`, `/spotify/login`,
  `/spotify/logout`, `/callback`, `/<folder>/`.
- POST: `/api/feedback`, `/api/queue`, `/api/more`, `/api/stop`,
  `/api/spotify/{play,pause,next,previous}`.

Gotchas worth knowing before changing anything here:

- **`_RUN_LOCK` allows exactly one discovery at a time, server-wide.** A second
  `/api/discover` request blocks (after emitting a "queued…" status) and
  `/api/more` takes the same lock.
- **Cancelling a run**: `POST /api/stop` just sets the module-level
  `_CANCEL_EVENT` — no lock needed, since the run's own `finally` releases
  `_RUN_LOCK` as it unwinds, which is what lets a queued second request
  proceed right after a stop. The flag is cleared right after a run acquires
  `_RUN_LOCK`, and checked inside `discover.py` at the same `say()`/
  `progress()` checkpoints already used for status messages (between every
  API hop in `discover_for_seed`, and every candidate in
  `resolve_to_spotify`/`resolve_to_youtube`) — each raises `discover.Cancelled`
  once the flag is set, which `quick.run`'s caller and `_run_folder`'s
  per-seed loop catch to unwind. `_run_folder` keeps seeds already streamed
  via `seed_html` before the stop (drops the interrupted one) and emits
  `type: "stopped"` instead of `"done"`, still persisting the partial page to
  `output/`. Only `search.html.j2` streams live progress (`index.html.j2` /
  `folder.html.j2` don't), so that's the only template with the Stop button
  and the `stopped` SSE handler. One `threading.Event` (not per-run) is
  enough because `_RUN_LOCK` already serializes runs to one at a time.
- **Default bind is `0.0.0.0`** so a phone on Tailscale/LAN can reach it
  (`--local` restricts it). Combined with `_local_path()`, which accepts a
  pasted absolute path as a dig target, anyone on those networks can point
  discovery at any local folder. That's a deliberate personal-tool trade-off,
  documented in the code; don't widen it casually.
- **The OAuth redirect URI embeds the port** and must match a Redirect URI
  registered on the Spotify app exactly. Spotify rejects the hostname
  `localhost`, hence the `127.0.0.1` literal — so serving on a non-default port
  needs a second registered URI.
- Folder runs (`_run_folder`) stream one `seed_html` event per finished seed
  and persist the full page at the end; if the tab dies mid-run, completed
  seeds are still written out.

### Templates (`templates/`)

Every page inlines `_style.css` via `{% include %}`, and every page that has
players (`recommendations`, `search`, `queue`) inlines `_player.js` the same
way — pages are entirely self-contained, and the server serves no static
assets. One macro set (`_cand_row.html.j2`, `_seed_block.html.j2`,
`_top_picks.html.j2`) is used by all three render paths (whole page, SSE
fragment, `/api/more` rows) so they cannot drift apart.

- **The Jinja environment sets `cache_size=0` on purpose.** With caching on,
  the compiled module of `_seed_block.html.j2` closed over a stale `cand_row`,
  so editing `_cand_row.html.j2` had no effect on a running server while CSS
  changes did — new styling around old markup. See the comment in `render.py`
  before "optimising" it back.
- **Archived run pages are frozen at generation time.** The inlined JS/CSS in
  an old `output/**/*.html` is whatever shipped that day; only the
  server-rendered pages (`/`, `/search`, `/queue`, `/<folder>/`) get today's
  code. Feedback and queue *state* is fetched from the server at load, so
  verdicts do appear on old pages, but new front-end behaviour does not.
- Anything needing the server (👍/👎, ⬇ queue, "Show more", Spotify Connect,
  YouTube embeds) no-ops or fails on a page opened via `file://`.
- `_player.js` carries browser-specific reasoning in its comments — the silent
  keeper `<audio>` must stay longer than 5 s (WebKit and Chromium media-session
  thresholds) and gets bounced on every queue advance to win Safari's
  most-recently-started ordering. Those constants are the fix, not incidental.

### Other things that will bite

- **Two Spotify modules, two auth flows, deliberately not merged.**
  `spotify.py` authenticates the *app* (catalog reads only); `spotify_connect.py`
  authenticates the *user* (playback, Premium-only). Embed playback was
  abandoned because the iframe only yields a 30 s preview without a
  third-party cookie browsers now block.
- **`spotify.Track` is the generic seed type**, used for local files and text
  seeds with `spotify_id=None`. The name is historical; don't read it as "this
  came from Spotify".
- `Candidate` and `TopPick` are `NamedTuple`s — the pipeline is immutable and
  resolution happens via `_replace`.
- **Read the comment block at the top of `discogs_scrape.py` before touching
  the scraper.** It records dated A/B results: request interception must stay
  off (it is itself a bot signal), the browser must be `channel="chromium"`
  rather than the headless shell, and the UA must be derived from the browser
  with only "Headless" stripped. It also keeps the `ScrapeUnavailable`
  exception distinct from an empty list so "we were blocked" is never cached as
  "this release has no recommendations".
- **Feedback is record-only.** Nothing in `discover.py` reads `feedback.jsonl`;
  ranking effects are an explicit future decision (see `TODO.md`).
- There is **no test suite and no linter config** in this repo. Verification is
  reading the code plus a manual run.

### Running it locally

`python3 -m venv .venv && source .venv/bin/activate && pip install -e .`, then
`playwright install chromium` (only needed for the Recommendations angle).
`DISCOGS_TOKEN` and `LASTFM_API_KEY` are required; Spotify credentials are
optional and their absence is a supported degraded mode. Full setup is in
`README.md`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
