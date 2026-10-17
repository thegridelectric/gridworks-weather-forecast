#!/bin/bash
# Bring up a dev weather service from nothing: an empty compose Postgres
# migrated to head, the actor and the API as d1.weather on the local
# dev broker, and the dev-records/ set minted over the bus in order.
# Idempotent: tears down any previous dev run first. Usage:
#   scripts/dev-up.sh          # then scripts/dev-down.sh when done
set -euo pipefail
cd "$(dirname "$0")/.."

set -a; . scripts/dev.env; set +a
export GWWF_G_NODE_PATH="$PWD/scripts/dev-g.node.gt.json"
case "$GWWF_RABBIT__URL" in
  *localhost*|*127.0.0.1*) ;;
  *) echo "refusing: GWWF_RABBIT__URL is not a local broker" >&2; exit 1 ;;
esac
if ! nc -z localhost 5672 2>/dev/null; then
  echo "dev broker not listening on localhost:5672 (start gw-dev-rabbit from gridworks-base)" >&2
  exit 1
fi

scripts/dev-down.sh
mkdir -p .dev
docker compose up -d --wait
uv run alembic upgrade head

uv run gwwf rabbit > .dev/rabbit.log 2>&1 &
echo $! > .dev/rabbit.pid
# The actor logs to the gwbase file logger, so readiness is the first
# create getting a verdict: each attempt waits VERDICT_TIMEOUT_S itself.
records=(dev-records/[0-9]*.json)
for _ in $(seq 1 6); do
  uv run gwwf create "${records[0]}" && break
  kill -0 "$(cat .dev/rabbit.pid)" 2>/dev/null || { echo "actor died; see .dev/rabbit.log" >&2; exit 1; }
done
for f in "${records[@]:1}"; do
  uv run gwwf create "$f"
done

uv run gwwf api > .dev/api.log 2>&1 &
echo $! > .dev/api.pid
echo "dev weather service up: actor pid $(cat .dev/rabbit.pid), api pid $(cat .dev/api.pid)"
echo "logs: .dev/rabbit.log .dev/api.log — records: curl -s localhost:8531/d1-weather/bundles"
