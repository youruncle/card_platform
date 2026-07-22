BEGIN;

INSERT INTO data_source (code, name, source_type, base_url)
VALUES (
    'cardhobby',
    '卡淘近期成交',
    'marketplace_search',
    'https://www.cardhobby.com.cn'
)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    source_type = EXCLUDED.source_type,
    base_url = EXCLUDED.base_url;

COMMIT;
