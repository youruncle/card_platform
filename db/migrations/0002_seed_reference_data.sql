BEGIN;

INSERT INTO brand (name, manufacturer, tier) VALUES
    ('National Treasures', 'Panini', 5),
    ('Crown Royale', 'Panini', 4),
    ('One and One', 'Panini', 5),
    ('Upper Deck', 'Upper Deck', 3)
ON CONFLICT (name) DO NOTHING;

INSERT INTO data_source (code, name, source_type) VALUES
    ('manual', 'Manual Entry', 'manual'),
    ('nba', 'NBA', 'player_stats'),
    ('market_import', 'Market Import', 'market_price'),
    ('news_import', 'News Import', 'news')
ON CONFLICT (code) DO NOTHING;

INSERT INTO scoring_model (name, version, description, weights, thresholds, active)
VALUES (
    'card_investment_score',
    '1.0.0',
    'Initial explainable basketball card investment score',
    '{
      "historical_standing": 0.20,
      "current_performance": 0.20,
      "age_potential": 0.15,
      "card_scarcity": 0.20,
      "brand_tier": 0.10,
      "market_heat": 0.15
    }'::jsonb,
    '{"strong_buy": 82, "buy": 70, "hold": 55, "reduce": 40}'::jsonb,
    true
)
ON CONFLICT (name, version) DO NOTHING;

COMMIT;
