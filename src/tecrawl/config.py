import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN", "")
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "")

USER_AGENT = "teCrawl/0.1 (+https://github.com/dreamspy/teCrawl)"

CACHE_DIR = PROJECT_ROOT / ".cache"
OUTPUT_DIR = PROJECT_ROOT / "output"


def missing_keys() -> list[str]:
    return [
        name
        for name, val in [
            ("SPOTIFY_CLIENT_ID", SPOTIFY_CLIENT_ID),
            ("SPOTIFY_CLIENT_SECRET", SPOTIFY_CLIENT_SECRET),
            ("DISCOGS_TOKEN", DISCOGS_TOKEN),
            ("LASTFM_API_KEY", LASTFM_API_KEY),
        ]
        if not val.strip()
    ]
