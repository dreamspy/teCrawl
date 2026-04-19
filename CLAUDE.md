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
