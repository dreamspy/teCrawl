"""Append-only "grab this later" download queue.

Each ⬇ click appends one JSON line to download_queue.jsonl at the project
root; nothing is ever rewritten, so the full history of what you queued and
un-queued stays auditable. The latest action per (artist, title) wins when
loading: 'add' queues it, 'remove' takes it back out.

Same shape as feedback.py deliberately — it's the proven pattern in this
codebase for a local, personal, append-only store, and the two files are
read the same way.

Local by design: a Spotify playlist would need OAuth this project doesn't
have and would only ever hold candidates that resolved to Spotify. This
covers every candidate, YouTube-only finds included (see TODO)."""

import json
import threading
from datetime import datetime

from . import config

QUEUE_PATH = config.PROJECT_ROOT / "download_queue.jsonl"

_LOCK = threading.Lock()
_ACTIONS = {"add", "remove"}
# Optional context stored alongside an entry, when the client sends it.
# Free text, truncated — a personal log, not a schema. The media ids ride
# along so the /queue page can offer playback without re-resolving anything.
_CONTEXT_FIELDS = (
    "source",
    "source_detail",
    "seed",
    "context",
    "page",
    "youtube_id",
    "spotify_id",
    "spotify_type",
)


def key(artist: str, title: str) -> str:
    return f"{artist.strip().lower()}||{title.strip().lower()}"


def record(entry: dict) -> dict:
    """Validate and append one queue line. Returns what was stored.
    Raises ValueError on bad input (missing artist/title, unknown action)."""
    action = entry.get("action")
    artist = str(entry.get("artist") or "").strip()
    title = str(entry.get("title") or "").strip()
    if action not in _ACTIONS or not artist or not title:
        raise ValueError(
            "download queue needs artist, title, and an action of add/remove"
        )
    stored = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "artist": artist[:300],
        "title": title[:300],
    }
    for k in _CONTEXT_FIELDS:
        v = str(entry.get(k) or "").strip()
        if v:
            stored[k] = v[:300]
    with _LOCK:
        with QUEUE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(stored, ensure_ascii=False) + "\n")
    return stored


def active() -> list[dict]:
    """Everything currently queued, in the order it was added. Latest action
    per (artist, title) wins; 'remove' drops the entry. Missing file → [].

    Dict insertion order does the ordering work: re-adding something already
    queued leaves it in place, while re-adding after a remove puts it at the
    end — which is what you'd expect without needing a sort field."""
    out: dict[str, dict] = {}
    try:
        lines = QUEUE_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        k = key(str(e.get("artist") or ""), str(e.get("title") or ""))
        if k == "||":
            continue
        a = e.get("action")
        if a == "remove":
            out.pop(k, None)
        elif a == "add":
            out[k] = e
    return list(out.values())


def keys() -> list[str]:
    """'artist||title' for everything queued — what the page JS needs to
    paint the ⬇ buttons without shipping the whole queue to every run page."""
    return [key(e.get("artist", ""), e.get("title", "")) for e in active()]
