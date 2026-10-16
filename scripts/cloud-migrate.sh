#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
dc() { sudo docker compose --env-file .env.cloud -f infra/compose.tunnel.yaml "$@"; }
dc up -d postgres
until dc exec -T postgres pg_isready -U card_platform -d card_platform >/dev/null 2>&1; do sleep 2; done
sql() { dc exec -T postgres psql -v ON_ERROR_STOP=1 -U card_platform -d card_platform "$@"; }
sql -c 'CREATE TABLE IF NOT EXISTS schema_migration (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())'
for file in db/migrations/*.sql; do
 version=$(basename "$file")
 applied=$(sql -tAc "SELECT 1 FROM schema_migration WHERE version='$version'")
 if [ "$applied" != 1 ]; then
  sql < "$file"
  sql -c "INSERT INTO schema_migration(version) VALUES ('$version')"
 fi
done
# Only the brand-new installation may remove migration-provided knowledge seeds.
initialized=$(sql -tAc "SELECT 1 FROM schema_migration WHERE version='cloud-empty-initialization-v1'")
if [ "$initialized" != 1 ]; then
 sql <<'SQL'
BEGIN;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM app_user) OR EXISTS (SELECT 1 FROM collection_item) THEN
  RAISE EXCEPTION 'Refusing empty initialization on a database with users or cards';
 END IF;
END $$;
UPDATE knowledge_workspace SET published_version_id=NULL;
DELETE FROM knowledge_workspace;
INSERT INTO knowledge_workspace(name) VALUES ('Card Platform');
INSERT INTO schema_migration(version) VALUES ('cloud-empty-initialization-v1');
COMMIT;
SQL
fi
