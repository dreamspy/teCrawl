# Save-to-Spotify-playlist: feasibility research (2026-08-30)

Research for the "Later — Save-to-Spotify-playlist" TODO item, reframed as: a
button on `/queue` that turns the download queue into a real Spotify
playlist, optionally organized into a "folder" in the user's account.

## Is creating a playlist via the API possible?

Yes — and the existing OAuth in `spotify_connect.py` is the right place to
extend, not a separate flow.

- `spotify_connect.py` already implements the authorization-code user-OAuth
  flow (`authorize_url()`, the `/callback` exchange, refresh-token storage in
  `.spotify_token.json`, an `_api()` helper with 401/403/429 handling). It
  currently requests only `user-read-playback-state
  user-modify-playback-state`, used for the `/queue` transport-bar playback
  control.
- Adding playlist creation means widening `SCOPES` to include
  `playlist-modify-private` (public only if that's ever wanted - private is
  the sensible default). This requires one re-consent (`show_dialog: true` is
  already forced, so this is just a disconnect/reconnect).
- **Current (Feb 2026) endpoint shapes** - the ones a from-memory or
  TODO-citation implementation would get wrong:
  - Create a playlist: `POST /me/playlists` (not the older
    `POST /users/{user_id}/playlists`, formally removed Feb 2026 -
    simpler, no `/me` profile lookup needed first).
  - Add tracks: `POST /playlists/{playlist_id}/items` (not the deprecated
    `POST /playlists/{playlist_id}/tracks`), body
    `{"uris": [...], "position": <int, optional>}`, max 100 URIs/call.
  - Both scopes: `playlist-modify-public` or `playlist-modify-private`.
  - Both slot naturally next to the existing `_api()` helper.

## Is the "folder" part feasible?

**No.** Spotify's Web API has no folder concept at all - no endpoint reads or
writes folder structure or playlist-to-folder placement. This is a
long-standing, still-open feature request against Spotify, not a gap in our
research. Folders are a client-side (desktop/mobile app) organizational layer
only.

Practical substitute: a consistent playlist naming convention (e.g.
`teCrawl queue · YYYY-MM-DD`) is the only thing the API can do toward
"organized" - the user would still need to manually drag a resulting
playlist into a folder in the Spotify app for that visual grouping.

## Current blocker: Spotify app credentials are dead

This blocks the feature regardless of the scope work above.
`spotify.py`'s client-credentials token flow currently fails
("Spotify app credentials rejected"), which parks Spotify API access via a
circuit breaker. Per `TODO.md`, the previous app was rate-limited then
deleted, and creating a replacement is currently blocked by an
account-level limit on the Spotify developer dashboard. `spotify_connect.py`
authenticates through that same app registration, so a new OAuth scope on a
dead app doesn't help - the app itself has to exist and be accepted by
Spotify's token endpoint first.

Possible root cause worth checking directly on the developer dashboard:
Spotify's Feb 2026 changes reportedly cap a developer account to one app
total. If so, the existing dead app may need to be fully removed
Spotify-side before a replacement can be created, not just waited out.
(This lead is unverified against the actual dashboard - flagged as a
starting point, not a confirmed cause.)

## How many queued tracks would be eligible right now?

Only tracks with a resolved `spotify_id` can be added to a playlist.
`dlqueue.py` entries already carry `spotify_id`/`spotify_type`/`youtube_id`
as optional context fields, and `queue.html.j2` already distinguishes them
(Spotify controls only render `{% if e.spotify_id %}`). Given Spotify
resolution has been failing entirely since at least 2026-08-28, the
realistic eligible count today is at or near zero. Even with resolution
fully working, historical rates were often only 0-3 of 20 candidates per
seed, so this feature will always cap out well under "everything queued,"
not because of a bug but because many Discogs/Last.fm finds simply don't
have a clean Spotify match.

## Recommendation

**Don't build this yet** - not because the idea is bad (the API/OAuth
mechanics are simple), but because it would ship a button that creates
empty or near-empty playlists while Spotify resolution is dead. Fixing that
is a prerequisite owned by the existing "Create a new Spotify app and test
the resolver fix" TODO item, not by this feature.

**Sequencing, once Spotify access is restored:**

1. Widen `spotify_connect.SCOPES` to add `playlist-modify-private`; one
   re-consent needed (cheap, `show_dialog=true` already forces it).
2. Add playlist creation/add-tracks calls, most naturally as functions in
   `spotify_connect.py` reusing its existing `_api()` helper rather than a
   new module.
3. UI: a single "Save to Spotify playlist" button on `/queue` (not a
   per-track checkbox - the point is "queuing is already the fast path"),
   next to the existing "Copy list" button. On click: filter queue entries
   to `spotify_id` truthy, show the count going in (e.g. "12 of 19 queued
   tracks are on Spotify"), and surface excluded (YouTube-only) tracks
   explicitly rather than silently dropping them - matches how the rest of
   the UI already shows inexact/empty results rather than hiding them. If
   zero are eligible, disable the button with a tooltip explaining why.
4. Naming: since there's no folder API, lean on a naming convention (e.g.
   `teCrawl queue · YYYY-MM-DD`), created private by default. Don't promise
   folder placement in the UI copy.
5. Open question for whoever implements this: should repeated clicks create
   a new playlist each time, or try to append to a previous one? There's no
   natural key tying a playlist to "the current queue state" - flagged here
   rather than decided.
