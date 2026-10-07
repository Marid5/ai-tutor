#!/usr/bin/env bash
# Update the running app to the latest origin/main and wait until it is healthy.
#
# Meant to be the forced command of the CI deploy key (see deploy/setup-server.md),
# so any arguments the SSH client sends are ignored. It can also be run by hand.
#
# Environment:
#   APP_DIR  checkout of the repository (default: /srv/ai-tutor)
#   PORT     host port the app listens on, loopback only (default: 8000)
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/ai-tutor}"
PORT="${PORT:-8000}"
HEALTH_URL="http://127.0.0.1:${PORT}/api/health"
HEALTH_TIMEOUT=60

# One deploy at a time; a second run exits instead of queueing behind the first.
exec 9>/tmp/ai-tutor-deploy.lock
flock -n 9 || { echo "deploy already running"; exit 1; }

cd "$APP_DIR"

if [ ! -f .env ]; then
  echo "Missing $APP_DIR/.env: create it from .env.example first (deploy/setup-server.md)." >&2
  exit 1
fi

git fetch origin main
git reset --hard origin/main
HEAD_SHA="$(git rev-parse HEAD)"
echo "Deploying $HEAD_SHA"

# The first deploy has no running container, hence nothing to back up. A failed
# backup stops the deploy: do not replace the app when its data is not safe.
running="$(docker compose ps --status running --services)"
if grep -qx app <<<"$running"; then
  docker compose exec -T app python -m app.cli backup
fi

GIT_SHA="$HEAD_SHA" docker compose up -d --build

# Healthy means: the answer comes from the commit just deployed, not from the
# old container that has not stopped yet.
for _ in $(seq 1 "$HEALTH_TIMEOUT"); do
  if curl -fsS --max-time 2 "$HEALTH_URL" 2>/dev/null | grep -Eq "\"git_sha\": ?\"${HEAD_SHA}\""; then
    echo "Healthy: $HEAD_SHA is serving."
    exit 0
  fi
  sleep 1
done

echo "Health check failed: $HEALTH_URL did not report $HEAD_SHA within ${HEALTH_TIMEOUT}s." >&2
docker compose logs --tail 50 app >&2
exit 1
