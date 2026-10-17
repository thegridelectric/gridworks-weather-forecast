#!/bin/bash
# Tear the dev weather service down: stop the actor and API started by
# dev-up.sh and remove the compose Postgres with its volume, so the next
# dev-up.sh starts from an empty database.
set -uo pipefail
cd "$(dirname "$0")/.."
for p in rabbit api; do
  if [ -f ".dev/$p.pid" ]; then
    kill "$(cat ".dev/$p.pid")" 2>/dev/null || true
    rm -f ".dev/$p.pid"
  fi
done
docker compose down -v >/dev/null 2>&1 || true
