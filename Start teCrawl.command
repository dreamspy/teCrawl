#!/usr/bin/env bash
# Double-click from Finder: runs open-output.sh in Terminal.app (starts the
# server, or just reopens the URL if it's already running). Finder launches
# .command files with cwd set to $HOME, so cd to the repo first.
cd "$(dirname "$0")" || exit 1
exec ./open-output.sh
