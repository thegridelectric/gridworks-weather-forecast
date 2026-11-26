#!/usr/bin/env bash
#
# The gate for this repo: the GitHub workflow runs this script and nothing
# else, so a local pass is a CI pass. Run it before committing.
#
# The tests need docker (testcontainers postgres for the DB layer; the
# integration tests boot the real actor on testcontainers rabbit + postgres
# and self-skip without it, so fail loudly here instead) and the dev broker
# on localhost:5672 (gridworks-base arm.sh / x86.sh; a service container
# in GitHub).
#
# Usage: ./ci.sh
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

step() { printf '\n=== %s ===\n' "$1"; shift; "$@"; }

step "uv sync (locked)" uv sync --all-groups --locked
step "ruff check" uv run ruff check --no-fix .
step "ruff format --check" uv run ruff format --check .
step "pyright" uv run pyright
step "sema snapshot staging check" uv run python -P \
  src/gwwf/sema/check_staging.py src/gwwf/sema_seed_request.yaml \
  --deploy-branch main
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: docker is not running; the test suite uses testcontainers." >&2
  exit 1
fi
step "tests" uv run pytest -q

printf '\nAll checks passed.\n'
