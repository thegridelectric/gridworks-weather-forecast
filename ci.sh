#!/bin/bash
# Run locally everything CI runs, so a push won't go red. Mirrors
# .github/workflows/tests.yml (lint + tests jobs). Usage: ./ci.sh
set -euo pipefail

step() { printf '\n=== %s ===\n' "$1"; shift; "$@"; }

# Resolve the env exactly as CI does.
step "uv sync (locked)" uv sync --all-groups --locked

# lint
step "ruff check" uv run ruff check --no-fix .
step "ruff format --check" uv run ruff format --check .

# tests need docker (testcontainers postgres for the DB layer; the DB
# tests self-skip without it, so fail loudly here instead).
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: docker is not running — the test suite uses testcontainers." >&2
  exit 1
fi
step "tests" uv run pytest -q

printf '\nAll CI checks passed.\n'
