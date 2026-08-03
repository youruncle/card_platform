# Codex 1000-card campaign handoff

This checkpoint moves the machine-assisted recognition campaign to another
computer without copying `.env`, browser profiles, credentials, or user upload
files.

## Checkpoint

- Completed optimization batches: 1–10 (`1000/1000`)
- Campaign state: complete
- Last completed batch ID: `7c473fcf-15c2-411f-9b40-fbb720076916`
- Last completed batch state: 100 samples predicted, 100 machine-labeled, 0 failures
- Resume pipeline: `image-v5.3.0`
- Recognition concurrency: `6`
- Manifest: `data/benchmarks/codex-campaign.json`
- Database archive: `data/benchmarks/card-platform-campaign-20260802-final.dump`
- SHA-256: `c886cc94bad177862e8dbcaf170ac2ebc5ea9b9768315de32f57c5c669cc0e54`

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
  data/benchmarks/card-platform-campaign-20260802-final.dump
```

Restore `.env` separately using the encrypted environment workflow documented in
the project README. Never commit the plaintext `.env`.

## Final audit

The completed campaign contains 1000 samples and 1000 distinct source item IDs,
with 1000 predictions, 1000 assisted labels and zero prediction failures. All
ten sampling configurations conform to the campaign scope, and no source item
overlaps across batches. Batch 10 ran on `image-v5.3.0` at concurrency 6 and
achieved provisional machine-assisted Top-1 accuracy 0.5361 and Top-3 recall
0.5567 on 97 scored samples. Its error mix did not support another safe broad
deterministic rewrite, so the final pipeline remains `image-v5.3.0`.

The final recognition gate passed 107 regression tests. Machine-assisted labels
are training diagnostics and are not a substitute for an independent human
acceptance set.

Batch 7 also fixed segmented historical replay: `replay_all` now skips samples
already written by the current pipeline version, so multiple 50-sample jobs
cover the full batch instead of repeatedly selecting the oldest page.
