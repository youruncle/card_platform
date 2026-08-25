BEGIN;

INSERT INTO data_source (code, name, source_type, base_url, metadata)
VALUES
    (
        'ebay_public', 'eBay public search', 'public_web', 'https://www.ebay.com',
        '{"priority_tier":2,"official_api":false,"max_evidence_grade":"E2"}'::jsonb
    ),
    (
        'sportscardspro', 'SportscardsPro', 'public_web', 'https://www.sportscardspro.com',
        '{"priority_tier":3,"official_api":false,"max_evidence_grade":"E2"}'::jsonb
    ),
    (
        'sports_card_investor', 'Sports Card Investor', 'public_web', 'https://www.sportscardinvestor.com',
        '{"priority_tier":3,"official_api":false,"max_evidence_grade":"E2"}'::jsonb
    ),
    (
        'public_market_research', 'Public market research', 'public_web', NULL,
        '{"priority_tier":3,"official_api":false,"max_evidence_grade":"E2"}'::jsonb
    )
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    source_type = EXCLUDED.source_type,
    base_url = EXCLUDED.base_url,
    metadata = data_source.metadata || EXCLUDED.metadata;

COMMIT;
