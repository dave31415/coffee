#!/usr/bin/env bash
# Launch the coffee viewer: starts the local server and opens the browser.
# Safe to symlink from a directory on your PATH.
set -euo pipefail
SELF="${BASH_SOURCE[0]}"
while [ -L "$SELF" ]; do
  DIR="$(cd -P "$(dirname "$SELF")" && pwd)"
  SELF="$(readlink "$SELF")"
  [[ "$SELF" != /* ]] && SELF="$DIR/$SELF"
done
cd "$(cd -P "$(dirname "$SELF")" && pwd)"
PORT="${PORT:-8765}"
exec .venv/bin/coffee serve --port "$PORT" --open "$@"
