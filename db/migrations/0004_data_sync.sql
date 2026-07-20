BEGIN;

CREATE TYPE sync_status AS ENUM ('running', 'completed', 'partial', 'failed');

CREATE TABLE data_sync_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id uuid NOT NULL REFERENCES player(id),
    status sync_status NOT NULL DEFAULT 'running',
    requested_sources text[] NOT NULL,
    source_results jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX data_sync_player_time_idx ON data_sync_run (player_id, started_at DESC);

INSERT INTO data_source (code, name, source_type, base_url) VALUES
    ('nba_stats', 'NBA Stats', 'player_stats', 'https://stats.nba.com/stats'),
    ('news_rss', 'Configured News RSS', 'news', NULL),
    ('injury_rss', 'Configured Injury RSS', 'injury', NULL)
ON CONFLICT (code) DO NOTHING;

COMMIT;
