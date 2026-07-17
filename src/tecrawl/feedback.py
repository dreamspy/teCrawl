"""Append-only 👍/👎 feedback store.

Each click appends one JSON line to feedback.jsonl at the project root;
nothing is ever rewritten, so the full history stays auditable. The latest
verdict per (artist, title) wins when loading, and 'clear' (clicking the
same thumb again) removes a verdict.

Deliberately record-only for now: feedback does NOT change ranking or
filtering yet. The decision was to collect real data first, then design
ranking effects on evidence (see TODO)."""

import json
import threading
from datetime import datetime

from . import config

FEEDBACK_PATH = config.PROJECT_ROOT / "feedback.jsonl"

_LOCK = threading.Lock()
_VERDICTS = {"up", "down", "clear"}
# Optional context fields stored alongside a verdict, when the client sends
# them. Free text, truncated — this is a personal log, not a schema.
# "comment" is the user's own reason ("totally irrelevant", "more like
# this"), the highest-signal field for tuning the algorithm later.
_CONTEXT_FIELDS = ("source", "source_detail", "seed", "context", "page", "comment")


def key(artist: str, title: str) -> str:
    return f"{artist.strip().lower()}||{title.strip().lower()}"


def record(entry: dict) -> dict:
    """Validate and append one feedback line. Returns what was stored.
    Raises ValueError on bad input (missing artist/title, unknown verdict)."""
    verdict = entry.get("verdict")
    artist = str(entry.get("artist") or "").strip()
    title = str(entry.get("title") or "").strip()
    if verdict not in _VERDICTS or not artist or not title:
        raise ValueError(
            "feedback needs artist, title, and a verdict of up/down/clear"
        )
    stored = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "verdict": verdict,
        "artist": artist[:300],
        "title": title[:300],
    }
    for k in _CONTEXT_FIELDS:
        v = str(entry.get(k) or "").strip()
        if v:
            stored[k] = v[:300]
    with _LOCK:
        with FEEDBACK_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(stored, ensure_ascii=False) + "\n")
    return stored


def latest() -> dict[str, str]:
    """'artist||title' (lowercase) → 'up' | 'down'. Latest line wins;
    'clear' removes the entry. Missing file → empty map."""
    out: dict[str, str] = {}
    try:
        lines = FEEDBACK_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return out
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
        v = e.get("verdict")
        if v == "clear":
            out.pop(k, None)
        elif v in ("up", "down"):
            out[k] = v
    return out
