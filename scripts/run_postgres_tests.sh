#!/usr/bin/env bash
# run_postgres_tests.sh — run the full test suite against a real Postgres 16 instance.
#
# Requires Docker. Used by CI (or locally via `make test-postgres`) to catch
# Postgres-specific bugs that the default SQLite test run misses.
#
# Exit code mirrors the test exit code.
#
# Make this script executable before use:
#   chmod +x scripts/run_postgres_tests.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"
docker compose -f docker-compose.postgres.yml up \
  --build \
  --abort-on-container-exit \
  --exit-code-from tests
EXIT_CODE=$?
docker compose -f docker-compose.postgres.yml down -v
exit $EXIT_CODE
