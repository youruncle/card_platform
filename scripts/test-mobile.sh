#!/usr/bin/env bash
# Disposable database and image directories only; never reads the local .env.
set -euo pipefail
cd "$(dirname "$0")/.."
mobile_pg_bin="${PG_BIN:-/opt/homebrew/opt/postgresql@16/bin}"
mobile_tmp="$(mktemp -d)"
mobile_port="${CARD_PLATFORM_TEST_PORT:-55439}"
trap '"$mobile_pg_bin/pg_ctl" -D "$mobile_tmp/db" stop -m fast >/dev/null 2>&1 || true; rm -rf "$mobile_tmp"' EXIT
"$mobile_pg_bin/initdb" -D "$mobile_tmp/db" --no-locale --encoding=UTF8 --auth=trust >/dev/null
"$mobile_pg_bin/pg_ctl" -D "$mobile_tmp/db" -l "$mobile_tmp/pg.log" -o "-p $mobile_port -h 127.0.0.1" start >/dev/null
"$mobile_pg_bin/createdb" -h 127.0.0.1 -p "$mobile_port" mobile_test
for migration in db/migrations/*.sql; do
  "$mobile_pg_bin/psql" -v ON_ERROR_STOP=1 -q -h 127.0.0.1 -p "$mobile_port" -d mobile_test -f "$migration" >/dev/null
done
export TEST_DATABASE_URL="postgresql://$(whoami)@127.0.0.1:$mobile_port/mobile_test"
export DATABASE_URL="$TEST_DATABASE_URL" UPLOAD_DIR="$mobile_tmp/uploads" SCANNER_JOB_DIR="$mobile_tmp/scanner" FLATBED_SCAN_DIR="$mobile_tmp/flatbed"
export PLAYER_PRICE_WORKER_ENABLED=false VALUATION_WORKER_ENABLED=false RECOGNITION_WORKER_ENABLED=false SCANNER_WORKER_ENABLED=false PURCHASE_COST_WORKER_ENABLED=false
export VISION_API_KEY='' AI_API_KEY='' AUTH_REGISTRATION_ENABLED=true
if [ "${MOBILE_BROWSER_CHECK:-0}" = "1" ]; then
  .venv/bin/python apps/api/tests/mobile_browser_check.py
  exit 0
fi
if [ "$#" -eq 0 ]; then
  set -- apps/api/tests/test_mobile_postgres.py apps/api/tests/test_auth.py apps/api/tests/test_private_uploads_postgres.py apps/api/tests/test_scanner_queue.py
fi
.venv/bin/pytest -q "$@"
