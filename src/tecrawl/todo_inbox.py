"""Append-only self-capture inbox for feature ideas / bugs noticed while
browsing — a raw personal scratchpad, separate from the curated TODO.md.

Each 💡 click appends one JSON line to todo_inbox.jsonl at the project root;
nothing is ever rewritten, so the full history stays auditable. The latest
action per id wins when loading: 'add' opens a note, 'resolve' marks it
handled once it's been folded into TODO.md or fixed — that folding step
itself stays manual (see TODO.md).

Same shape as feedback.py/dlqueue.py deliberately — it's the proven pattern
in this codebase for a local, personal, append-only store."""

import json
import threading
import uuid
from datetime import datetime

from . import config

INBOX_PATH = config.PROJECT_ROOT / "todo_inbox.jsonl"

_LOCK = threading.Lock()
_ACTIONS = {"add", "resolve"}
# Optional context stored alongside a note, when the client sends it. Free
# text, truncated — this is a personal log, not a schema. "page" is the path
# it was captured from; "seed" is filled in when the button is clicked from a
# candidate row rather than a generic nav spot.
_CONTEXT_FIELDS = ("page", "seed")


def record(entry: dict) -> dict:
    """Validate and append one inbox line. Returns what was stored.
    Raises ValueError on bad input (unknown action, empty text/id)."""
    action = entry.get("action")
    if action not in _ACTIONS:
        raise ValueError("todo inbox needs an action of add/resolve")
    if action == "add":
        text = str(entry.get("text") or "").strip()
        if not text:
            raise ValueError("a todo note needs some text")
        stored = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "action": "add",
            "id": uuid.uuid4().hex[:12],
            "text": text[:2000],
        }
        for k in _CONTEXT_FIELDS:
            v = str(entry.get(k) or "").strip()
            if v:
                stored[k] = v[:300]
    else:  # resolve
        entry_id = str(entry.get("id") or "").strip()
        if not entry_id:
            raise ValueError("resolve needs an id")
        stored = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "action": "resolve",
            "id": entry_id,
        }
    with _LOCK:
        with INBOX_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(stored, ensure_ascii=False) + "\n")
    return stored


def open_entries() -> list[dict]:
    """Everything not yet resolved, in the order added. Latest action per id
    wins; 'resolve' drops the entry. Missing file → []."""
    out: dict[str, dict] = {}
    try:
        lines = INBOX_PATH.read_text(encoding="utf-8").splitlines()
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
        entry_id = e.get("id")
        if not entry_id:
            continue
        if e.get("action") == "resolve":
            out.pop(entry_id, None)
        elif e.get("action") == "add":
            out[entry_id] = e
    return list(out.values())
