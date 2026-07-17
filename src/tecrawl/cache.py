import hashlib
import json
import time
from typing import Any

from . import config

CACHE_TTL_SECONDS = 7 * 24 * 3600


def _key(url: str, params: dict | None) -> str:
    payload = json.dumps({"url": url, "params": params or {}}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def get(url: str, params: dict | None = None, ttl: int = CACHE_TTL_SECONDS) -> Any | None:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = config.CACHE_DIR / f"{_key(url, params)}.json"
    if not path.exists():
        return None
    try:
        with path.open() as f:
            entry = json.load(f)
        if entry.get("ts", 0) + ttl < time.time():
            return None
        return entry["data"]
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def put(url: str, params: dict | None, data: Any) -> None:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = config.CACHE_DIR / f"{_key(url, params)}.json"
    with path.open("w") as f:
        json.dump({"ts": time.time(), "data": data}, f)
