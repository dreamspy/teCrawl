"""Turn a folder of local audio files into seed Tracks.

Tags first (mutagen reads ID3, MP4, Vorbis/FLAC, ASF, …), filename parsing
as the fallback for untagged files. No API calls — Spotify/Discogs matching
happens later in the normal discovery pipeline — but not necessarily fast: a
folder under a cloud-sync mount (Dropbox/iCloud CloudStorage) can add real
per-file latency reading tags, even for files already "downloaded".

Filename patterns understood (after stripping a leading track number like
"01 - " / "01. " / vinyl "A1 ", and Bandcamp-style underscores):
  Artist - Title.mp3
  03. Artist - Title (Some Remix) [LABEL001].flac
Files with neither usable tags nor an "Artist - Title" filename are
reported as skipped, never guessed at.
"""

import re
from pathlib import Path
from typing import NamedTuple

import mutagen

from . import spotify
from .cancel import Cancelled

AUDIO_EXTS = {
    ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus",
    ".wav", ".aif", ".aiff", ".wma", ".wv", ".ape",
}


class ScannedTrack(NamedTuple):
    track: spotify.Track
    how: str  # "tags" | "filename"
    path: Path


class FolderScan(NamedTuple):
    name: str  # folder name — becomes the playlist name
    tracks: list[ScannedTrack]
    skipped: list[tuple[Path, str]]  # (file, human-readable reason)


# "01 - ", "01. ", "01_", and vinyl positions "A1 - ". Requires a separator
# char after the number so artists like "808 State" or "100gecs" never lose
# their name. _VINYLPOS_RE covers the space-only variant ("A1 Surgeon - …").
_TRACKNO_RE = re.compile(r"^\s*(?:\d{1,3}|[A-Da-d]\d{1,2})\s*[-._)\]]\s*")
_VINYLPOS_RE = re.compile(r"^\s*[A-Da-d]\d{1,2}\s+")
_DASH_SPLIT_RE = re.compile(r"\s+[-–—~]\s+")
_TRAILING_BRACKET_RE = re.compile(r"\s*\[[^\]]*\]\s*$")  # "… [Label / CAT001]"

# Easy-mode keys first (EasyID3 / EasyMP4 / Vorbis), then raw frame names
# for formats without an easy wrapper (ID3-in-WAV/AIFF, ASF/WMA).
_ARTIST_TAGS = ["artist", "albumartist", "TPE1", "TPE2", "©ART", "aART", "Author"]
_TITLE_TAGS = ["title", "TIT2", "©nam", "Title"]


def _tag_first(tags, keys: list[str]) -> str | None:
    if not tags:
        return None
    for k in keys:
        try:
            v = tags.get(k)
        except Exception:
            v = None
        if v is None or v == []:
            continue
        if isinstance(v, (list, tuple)):
            v = v[0]
        s = str(v).strip()
        if s:
            return s
    return None


def _read_tags(path: Path) -> tuple[str, str] | None:
    artist = title = None
    for easy in (True, False):
        try:
            f = mutagen.File(path, easy=easy)
        except Exception:
            continue
        if f is None:
            continue
        artist = artist or _tag_first(f.tags, _ARTIST_TAGS)
        title = title or _tag_first(f.tags, _TITLE_TAGS)
        if artist and title:
            return artist, title
    return None


def _try_split(s: str) -> tuple[str, str] | None:
    parts = _DASH_SPLIT_RE.split(s, maxsplit=1)
    if len(parts) != 2:
        return None
    artist = re.sub(r"\s+", " ", parts[0]).strip()
    title = _TRAILING_BRACKET_RE.sub("", parts[1])
    title = re.sub(r"\s+", " ", title).strip()
    # A bare track number is never an artist ("07 - Intro.mp3").
    if not artist or not title or re.fullmatch(r"\d{1,3}", artist):
        return None
    return artist, title


def _parse_filename(stem: str) -> tuple[str, str] | None:
    # Prefix-stripped variants first (only those that actually removed
    # something), raw last: "01 - Artist - Title" parses after the strip,
    # while "B12 - Obtuse" (artist that LOOKS like a vinyl position — the
    # strip leaves nothing to dash-split) falls through to the raw parse.
    for base in (stem, stem.replace("_", " ")):
        base = base.strip()
        variants: list[str] = []
        for rx in (_TRACKNO_RE, _VINYLPOS_RE):
            s = rx.sub("", base).strip()
            if s and s != base and s not in variants:
                variants.append(s)
        variants.append(base)
        for s in variants:
            got = _try_split(s)
            if got:
                return got
    return None


def seed_from_file(path: Path) -> tuple[str, str, str] | None:
    """(artist, title, how) for a single audio file, or None if unreadable."""
    got = _read_tags(path)
    if got:
        return got[0], got[1], "tags"
    got = _parse_filename(path.stem)
    if got:
        return got[0], got[1], "filename"
    return None


def scan(folder: Path, cancel=None) -> FolderScan:
    """Recursive scan; hidden files/dirs (incl. macOS ._AppleDouble) skipped;
    duplicate (artist, title) pairs collapse to the first file seen.

    No API calls, but not necessarily instant: a folder under a cloud-sync
    mount (Dropbox/iCloud CloudStorage) can have real per-file latency even
    once "downloaded", since reading a file's tags still goes through that
    provider's virtual filesystem layer. `cancel` (optional, a
    `threading.Event`) is checked once per file so a Stop click during a big
    scan doesn't have to wait for every remaining file first — raises
    `Cancelled`."""
    files = sorted(
        (
            p
            for p in folder.rglob("*")
            if p.is_file()
            and p.suffix.lower() in AUDIO_EXTS
            and not any(part.startswith(".") for part in p.relative_to(folder).parts)
        ),
        key=lambda p: str(p).lower(),
    )
    tracks: list[ScannedTrack] = []
    skipped: list[tuple[Path, str]] = []
    seen: dict[tuple[str, str], Path] = {}
    for p in files:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        got = seed_from_file(p)
        if not got:
            skipped.append(
                (p, "no artist/title tags and the filename isn't 'Artist - Title'")
            )
            continue
        artist, title, how = got
        key = (artist.lower(), title.lower())
        if key in seen:
            skipped.append((p, f"duplicate of {seen[key].name}"))
            continue
        seen[key] = p
        tracks.append(
            ScannedTrack(
                spotify.Track(artist=artist, title=title, spotify_id=None), how, p
            )
        )
    return FolderScan(folder.resolve().name, tracks, skipped)
