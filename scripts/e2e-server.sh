#!/usr/bin/env bash
# Serve the built client and the API on a throwaway database for the browser
# tests. Started by frontend/playwright.config.ts; not meant for production.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "e2e-server: .venv/bin/uvicorn not found; run 'make setup' first." >&2
  exit 1
fi

# Always rebuild, so the tests never run against a stale bundle.
make build

DB_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ai-tutor-e2e.XXXXXX")"
SERVER=""
cleanup() {
  if [ -n "$SERVER" ]; then kill "$SERVER" 2>/dev/null || true; wait "$SERVER" 2>/dev/null || true; fi
  rm -rf "$DB_DIR"
}
trap cleanup EXIT
trap 'exit 143' INT TERM

export DATABASE_PATH="$DB_DIR/ai_tutor.db"
export CONTENT_DIR="$ROOT/content"
export REGISTRATION=open
# Plain http on 127.0.0.1: a Secure cookie would never be sent back.
export COOKIE_SECURE=false
# Every test signs up a fresh account from the same address.
export REGISTER_LIMIT_PER_HOUR=10000
export LOGIN_IP_LIMIT=10000

# Not exec: the shell stays to remove the database once the server stops.
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${E2E_PORT:-8765}" &
SERVER=$!
wait "$SERVER"
