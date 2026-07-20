#!/usr/bin/env bash
set -euo pipefail

task_pg_bin="${PG_BIN:-}"
if [[ -z "$task_pg_bin" ]] && command -v brew >/dev/null 2>&1; then
  task_pg_bin="$(brew --prefix postgresql@16 2>/dev/null)/bin"
fi
if [[ -z "$task_pg_bin" || ! -x "$task_pg_bin/initdb" ]]; then
  echo "PostgreSQL 16 tools not found. Set PG_BIN or install postgresql@16." >&2
  exit 1
fi

task_pg_port="${CARD_PLATFORM_TEST_PORT:-55433}"
task_pg_tmp="$(mktemp -d)"
task_pg_data="$task_pg_tmp/data"
task_pg_log="$task_pg_tmp/postgres.log"

cleanup() {
  if [[ -d "$task_pg_data" ]]; then
    "$task_pg_bin/pg_ctl" -D "$task_pg_data" stop -m fast >/dev/null 2>&1 || true
  fi
  rm -rf "$task_pg_tmp"
}
trap cleanup EXIT

"$task_pg_bin/initdb" -D "$task_pg_data" --no-locale --encoding=UTF8 --auth=trust >/dev/null
"$task_pg_bin/pg_ctl" -D "$task_pg_data" -l "$task_pg_log" \
  -o "-p $task_pg_port -h 127.0.0.1" start >/dev/null
"$task_pg_bin/createdb" -h 127.0.0.1 -p "$task_pg_port" card_platform_test

for migration in db/migrations/*.sql; do
  "$task_pg_bin/psql" -v ON_ERROR_STOP=1 -q \
    -h 127.0.0.1 -p "$task_pg_port" -d card_platform_test -f "$migration"
done

TEST_DATABASE_URL="postgresql://$(whoami)@127.0.0.1:$task_pg_port/card_platform_test" \
  .venv/bin/pytest -q apps/api/tests
