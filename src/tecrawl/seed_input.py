"""Turn whatever the user pastes into a single seed Track.

Accepted inputs:
- Spotify track or album link/URI (playlist and artist links get a helpful error)
- YouTube link (watch / youtu.be / shorts / music.youtube.com)
- "Artist - Title" text
- free-text search

Resolution prefers the Spotify Web API but degrades to no-auth paths (the
Spotify embed page, Last.fm track.search) so everything keeps working while
the API app is dead."""

import html
import re
from typing import Callable, NamedTuple

from . import lastfm, spotify, youtube


class InputError(ValueError):
    """User-facing: the input couldn't be turned into a seed track."""


class ResolvedSeed(NamedTuple):
    track: spotify.Track
    note: str  # human-readable "how we read your input"


_SPOTIFY_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?|spotify:)"
    r"(track|album|playlist|artist)[/:]([A-Za-z0-9]{22})",
    re.IGNORECASE,
)
_YOUTUBE_ID_RE = re.compile(r"(?:youtu\.be/|/shorts/|[?&]v=)([A-Za-z0-9_-]{11})")
_DASH_SPLIT_RE = re.compile(r"\s+[-–—~]\s+")

# Bracketed segments that are video-page noise, not part of the track title.
# Only fires when the FIRST word inside the bracket is a noise keyword, so
# "(Original Mix)" and "(Blawan Remix)" survive.
_NOISE_BRACKET_RE = re.compile(
    r"[(\[{]\s*(?:official|officiell?|hd|hq|4k|full|free|premiere|première"
    r"|out\s+now|music\s+video|lyric|lyrics|visuali[sz]er|audio|video"
    r"|videoclip|stream|download|snippet|teaser|preview|promo)\b[^)\]}]*[)\]}]",
    re.IGNORECASE,
)
_PREFIX_NOISE_RE = re.compile(
    r"^\s*(?:premiere|première|first\s+play|exclusive|full\s+stream)\s*[:|\-–—]\s*",
    re.IGNORECASE,
)
_TRAILING_BRACKET_RE = re.compile(r"\s*\[[^\]]*\]\s*$")  # "… [Label / CAT001]"


def _clean_video_title(title: str) -> str:
    t = html.unescape(title)
    t = _PREFIX_NOISE_RE.sub("", t)
    t = _NOISE_BRACKET_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" -–—|· ").strip()


def _canonicalize(artist: str, title: str) -> tuple[spotify.Track, bool]:
    """Try to swap a parsed (artist, title) for Spotify's canonical spelling,
    which also gains a playable spotify_id. Falls back to the parsed values."""
    try:
        hit = spotify.search_track(artist, title)
    except Exception:
        hit = None
    if hit:
        return hit, True
    return spotify.Track(artist=artist, title=title, spotify_id=None), False


def _seed_from_spotify(kind: str, spotify_id: str) -> ResolvedSeed:
    if kind == "playlist":
        raise InputError(
            "That's a playlist link — quick search takes a single track. "
            "For a whole playlist, export a CSV via Exportify and run "
            "`tecrawl <csv>`."
        )
    if kind == "artist":
        raise InputError("That's an artist link — paste a track (or album) link.")
    if kind == "track":
        track = spotify.get_track(spotify_id)
        if not track:
            raise InputError(
                "Couldn't read that Spotify track link (bad link, or Spotify "
                "unreachable). Try typing it as 'Artist - Title'."
            )
        return ResolvedSeed(track, "from your Spotify link")
    # album
    got = spotify.get_album_seed(spotify_id)
    if not got:
        raise InputError(
            "Couldn't read that Spotify album link. Try a track link, or type "
            "'Artist - Title'."
        )
    track, album_title = got
    note = "album link · seeding from its first track"
    if album_title and track.title == album_title:
        note = "album link · couldn't list tracks, seeding on the album title"
    return ResolvedSeed(track, note)


def _seed_from_youtube(video_id: str) -> ResolvedSeed:
    info = youtube.video_info(video_id)
    if not info:
        raise InputError(
            "Couldn't read that YouTube video (private or removed?). "
            "Try typing it as 'Artist - Title'."
        )
    raw_title = html.unescape(info.get("title") or "").strip()
    author = html.unescape(info.get("author") or "").strip()

    if author.lower().endswith(" - topic"):
        # Auto-generated music channel: author is "Artist - Topic" and the
        # video title is the exact track title. The most reliable case.
        artist = author[: -len(" - topic")].strip()
        title = _clean_video_title(raw_title)
        how = "auto-generated music channel"
    else:
        cleaned = _clean_video_title(raw_title)
        parts = _DASH_SPLIT_RE.split(cleaned, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            artist = parts[0].strip()
            title = _TRAILING_BRACKET_RE.sub("", parts[1]).strip()
            how = "video title"
        else:
            # No dash — assume the channel IS the artist (true for most
            # artist-owned uploads; wrong for label/premiere channels, where
            # the Spotify verify below usually still rescues it).
            artist = author
            title = _TRAILING_BRACKET_RE.sub("", cleaned).strip()
            how = "video title + channel name"

    if not (artist and title):
        raise InputError(
            "Couldn't parse an artist and title from that YouTube video. "
            "Try typing it as 'Artist - Title'."
        )
    track, verified = _canonicalize(artist, title)
    track = track._replace(youtube_id=video_id)
    note = f"from YouTube ({how})" + (" · verified on Spotify" if verified else "")
    return ResolvedSeed(track, note)


def _seed_from_text(q: str) -> ResolvedSeed:
    parts = _DASH_SPLIT_RE.split(q, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        artist, title = parts[0].strip(), parts[1].strip()
        track, verified = _canonicalize(artist, title)
        note = (
            "matched on Spotify"
            if verified
            else "using your text as-is (no Spotify match)"
        )
        return ResolvedSeed(track, note)

    # No dash: free search. Spotify first, Last.fm as the keyless fallback.
    try:
        hit = spotify.search_track_freetext(q)
    except Exception:
        hit = None
    if hit:
        return ResolvedSeed(hit, "best Spotify match")
    found = None
    try:
        found = lastfm.search_track(q)
    except Exception:
        found = None
    if found:
        artist, title = found
        track, _ = _canonicalize(artist, title)
        return ResolvedSeed(track, "best Last.fm match")
    raise InputError(
        "Couldn't find that track. Try the format 'Artist - Title', or paste "
        "a Spotify/YouTube link."
    )


def resolve(query: str, progress: Callable[[str], None] | None = None) -> ResolvedSeed:
    """Main entry: query string in, ResolvedSeed out. Raises InputError with
    a user-facing message when the input can't be understood."""
    say = progress or (lambda m: None)
    q = query.strip()
    if not q:
        raise InputError("Type a track name or paste a Spotify/YouTube link.")

    m = _SPOTIFY_RE.search(q)
    yt = None
    if not m and ("youtube." in q.lower() or "youtu.be" in q.lower()):
        yt = _YOUTUBE_ID_RE.search(q)

    if m:
        say("Reading the Spotify link…")
        seed = _seed_from_spotify(m.group(1).lower(), m.group(2))
    elif yt:
        say("Reading the YouTube video info…")
        seed = _seed_from_youtube(yt.group(1))
    elif "://" in q or q.lower().startswith("www."):
        raise InputError(
            "Only Spotify and YouTube links are supported — or type the track "
            "as 'Artist - Title'."
        )
    else:
        say("Looking up the track…")
        seed = _seed_from_text(q)

    if not seed.track.youtube_id:
        # Give the seed a playable YouTube button even when Spotify can't
        # play it (dead API creds, or the track simply isn't on Spotify).
        say("Finding a YouTube video for the seed…")
        try:
            vid = youtube.search_video_id(seed.track.artist, seed.track.title)
        except Exception:
            vid = None
        if vid:
            seed = ResolvedSeed(seed.track._replace(youtube_id=vid), seed.note)
    return seed
