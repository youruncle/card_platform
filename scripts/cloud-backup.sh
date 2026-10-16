#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
umask 077
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
sudo docker compose --env-file .env.cloud -f infra/compose.tunnel.yaml exec -T postgres pg_dump -U card_platform -d card_platform -Fc > "backups/db-$stamp.dump"
test -s "backups/db-$stamp.dump"
printf 'Database backup: %s\n' "backups/db-$stamp.dump"
# Backups remain on this host; copy them off-host separately.
