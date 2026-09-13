#!/bin/bash
# Thin macOS entry point -- all real logic lives in start_quillwarden.py
# (shared with Windows via start_quillwarden.bat) so both platforms stay
# in sync automatically instead of drifting apart as two separate scripts.
cd "$(dirname "$0")"

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "Quillwarden needs Python 3, which wasn't found on this Mac."
  echo "Install it from https://python.org (or 'brew install python3') and run this again."
  read -p "Press Return to close this window..."
  exit 1
fi

"$PY" start_quillwarden.py
