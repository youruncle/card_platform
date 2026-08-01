BEGIN;

UPDATE data_source
SET name = '卡淘 / Card Hobby',
    metadata = metadata || '{
      "aliases": ["卡淘", "Card Hobby"],
      "integration_mode": "public_endpoint",
      "readiness": "active",
      "double_count_group": "cardhobby"
    }'::jsonb
WHERE code = 'cardhobby';

INSERT INTO data_source (code, name, source_type, base_url, active, metadata)
VALUES
    (
        'ebay',
        'eBay',
        'authorized_api',
        'https://www.ebay.com',
        true,
        '{
          "integration_mode": "official_marketplace_insights_api",
          "readiness": "credentials_required",
          "sold_data_scope_required": true
        }'::jsonb
    ),
    (
        '130point',
        '130 Point',
        'authorized_import',
        'https://130point.com',
        true,
        '{
          "integration_mode": "licensed_or_user_authorized_export",
          "readiness": "import_ready",
          "aggregates_other_marketplaces": true
        }'::jsonb
    ),
    (
        'weikajia',
        '微卡家',
        'authorized_import',
        'https://apps.apple.com/cn/app/id6482996329',
        true,
        '{
          "integration_mode": "partner_api_or_user_authorized_export",
          "readiness": "import_ready",
          "mobile_app_only": true
        }'::jsonb
    )
ON CONFLICT (code) DO UPDATE
SET name = EXCLUDED.name,
    source_type = EXCLUDED.source_type,
    base_url = EXCLUDED.base_url,
    active = EXCLUDED.active,
    metadata = data_source.metadata || EXCLUDED.metadata;

COMMIT;
