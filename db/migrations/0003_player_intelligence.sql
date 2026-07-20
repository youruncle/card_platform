BEGIN;

CREATE TABLE player_potential_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id uuid NOT NULL REFERENCES player(id),
    assessed_at timestamptz NOT NULL DEFAULT now(),
    potential_score numeric(5,2) NOT NULL CHECK (potential_score BETWEEN 0 AND 100),
    label text NOT NULL,
    components jsonb NOT NULL,
    inputs jsonb NOT NULL,
    model_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX player_potential_player_time_idx
    ON player_potential_snapshot (player_id, assessed_at DESC);

COMMIT;
