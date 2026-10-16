BEGIN;
CREATE TABLE player_price_source_conflict (
    source_id text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE player_price_sale (
    source_id text PRIMARY KEY,
    player_id uuid NOT NULL REFERENCES player(id),
    card_key text NOT NULL,
    identity jsonb NOT NULL,
    sale_price numeric(18,2) NOT NULL CHECK (sale_price > 0),
    occurred_at timestamptz NOT NULL,
    source_url text NOT NULL,
    raw_title text NOT NULL,
    excluded boolean NOT NULL DEFAULT false,
    observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX player_price_sale_player_date_idx ON player_price_sale(player_id, occurred_at DESC)
    WHERE NOT excluded;
CREATE TABLE player_price_basket (
    player_id uuid NOT NULL REFERENCES player(id),
    month date NOT NULL,
    selected_on date NOT NULL,
    cards jsonb NOT NULL CHECK (jsonb_array_length(cards) = 3),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(player_id,month)
);
CREATE TABLE player_price_snapshot (
    player_id uuid NOT NULL REFERENCES player(id),
    report_date date NOT NULL,
    result jsonb NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(player_id,report_date)
);
CREATE TABLE player_price_state (
    player_id uuid PRIMARY KEY REFERENCES player(id),
    requested_at timestamptz,
    last_attempt_at timestamptz,
    last_success_at timestamptz,
    last_report_date date,
    error text,
    coverage jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX player_price_state_due_idx ON player_price_state(last_report_date,last_attempt_at);
COMMIT;
