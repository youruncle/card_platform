#!/usr/bin/env bash
# Local-only checks; no database migration or cloud publication.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd apps/web && node --import tsx --test tests/*.test.cjs && npm run build)
(cd apps/mobile && npm test && npm run build)
