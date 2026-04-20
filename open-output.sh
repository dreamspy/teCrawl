#!/usr/bin/env bash
# Open the latest tecrawl output in your browser. If the server is already
# running on PORT, just open the URL; otherwise activate the venv and start
# it (the server opens the browser on startup itself — see cli.py _serve).
set -e
cd "$(dirname "$0")"

PORT="${PORT:-8765}"
URL="http://localhost:${PORT}/"

if lsof -iTCP:"$PORT" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
  echo "Server already running on port ${PORT}. Opening ${URL}"
  open "$URL"
  exit 0
fi

source .venv/bin/activate
exec tecrawl serve "$PORT"
