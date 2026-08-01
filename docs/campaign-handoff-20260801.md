# Codex 1000-card campaign handoff

This checkpoint moves the machine-assisted recognition campaign to another
computer without copying `.env`, browser profiles, credentials, or user upload
files.

## Checkpoint

- Completed optimization batches: 1–5 (`500/1000`)
- Active batch: 6
- Active batch ID: `ef2f66fc-254b-499f-b54d-ce5a14ae235b`
- Active batch state: 100 samples collected, 0 predicted, 0 labeled
- Resume pipeline: `image-v5.0.0`
- Manifest: `data/benchmarks/codex-campaign.json`
- Database archive: `data/benchmarks/card-platform-campaign-20260801.dump`
- SHA-256: `944c9dafc80ea48284c4fab2f23a00e87ff54b393cfdc9a7ee20424ec07093b1`

The archive contains only recognition benchmark, knowledge, and market evidence
table data. It does not contain environment secrets or browser session data.

## Restore on a new computer

Start with an empty database, configure `DATABASE_URL`, and apply every committed
migration first:

```bash
for migration in db/migrations/*.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$migration"
done
```

Restore the checkpoint. `--disable-triggers` is required because the knowledge
version/entity tables intentionally contain circular foreign keys; use the local
PostgreSQL owner/superuser for this one restore operation.

```bash
pg_restore \
  --data-only \
  --disable-triggers \
  --no-owner \
  --exit-on-error \
  --dbname "$DATABASE_URL" \
  data/benchmarks/card-platform-campaign-20260801.dump
```

Restore `.env` separately using the encrypted environment workflow documented in
the project README. Never commit the plaintext `.env`.

## Resume batch 6

After starting PostgreSQL and the API, verify the batch before prediction:

```bash
curl http://127.0.0.1:8000/v1/recognition-benchmarks/ef2f66fc-254b-499f-b54d-ce5a14ae235b/sampling-audit
```

Then run:

```bash
CODEX_CAMPAIGN_POLL_MS=5000 \
node scripts/run-codex-campaign-batch.mjs \
  ef2f66fc-254b-499f-b54d-ce5a14ae235b
```

Do not start batch 7 until batch 6 has completed prediction, machine labeling,
error attribution, code optimization, regression tests, and historical replay.
