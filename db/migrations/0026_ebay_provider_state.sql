BEGIN;

INSERT INTO provider_state (
    provider_code, concurrent_limit, min_interval_ms, error_budget
)
SELECT code, 2, 500, 5
FROM data_source
WHERE code = 'ebay'
ON CONFLICT (provider_code) DO NOTHING;

COMMIT;
