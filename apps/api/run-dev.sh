#!/usr/bin/env bash
# Launch the API in dev mode with the macOS Homebrew lib path exposed so
# WeasyPrint can load Pango/Cairo. On Linux (Fly.io) this isn't needed.
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname)" == "Darwin" && -d /opt/homebrew/lib ]]; then
  export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
fi

exec .venv/bin/uvicorn job_os.main:app --reload "$@"
