CREATE TABLE IF NOT EXISTS player_news_alert (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id uuid NOT NULL REFERENCES player(id),
    event_key text NOT NULL CHECK (length(trim(event_key)) > 0),
    direction text NOT NULL CHECK (direction IN ('positive', 'negative')),
    headline text NOT NULL,
    facts text NOT NULL,
    attribution text NOT NULL,
    confidence text NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
    source_url text NOT NULL CHECK (source_url ~ '^https?://'),
    published_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '72 hours',
    UNIQUE (player_id, event_key),
    CHECK (expires_at = created_at + interval '72 hours')
);
CREATE INDEX IF NOT EXISTS player_news_alert_active_idx
    ON player_news_alert(player_id, expires_at);
