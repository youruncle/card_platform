BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE transaction_type AS ENUM ('buy', 'sell', 'fee', 'adjustment');
CREATE TYPE valuation_method AS ENUM ('market_comp', 'listing', 'manual', 'model', 'ai_estimate');
CREATE TYPE player_event_type AS ENUM ('news', 'injury', 'trade', 'team_context', 'award', 'milestone');
CREATE TYPE recommendation_rating AS ENUM ('strong_buy', 'buy', 'hold', 'reduce', 'avoid');
CREATE TYPE analysis_status AS ENUM ('pending', 'running', 'completed', 'failed');

CREATE TABLE app_user (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL UNIQUE,
    display_name text NOT NULL,
    base_currency char(3) NOT NULL DEFAULT 'CNY',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE player (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name text NOT NULL,
    normalized_name text NOT NULL,
    birth_date date,
    nba_person_id bigint UNIQUE,
    primary_position text,
    active boolean NOT NULL DEFAULT true,
    hall_of_fame boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (normalized_name, birth_date)
);

CREATE INDEX player_normalized_name_idx ON player (normalized_name);
CREATE UNIQUE INDEX player_name_without_birth_date_uidx
    ON player (normalized_name) WHERE birth_date IS NULL;

CREATE TABLE team (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    league text NOT NULL DEFAULT 'NBA',
    name text NOT NULL,
    abbreviation text NOT NULL,
    external_id text,
    active boolean NOT NULL DEFAULT true,
    UNIQUE (league, abbreviation)
);

CREATE TABLE player_team_tenure (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id uuid NOT NULL REFERENCES player(id),
    team_id uuid NOT NULL REFERENCES team(id),
    start_date date NOT NULL,
    end_date date,
    jersey_number text,
    CHECK (end_date IS NULL OR end_date >= start_date),
    UNIQUE (player_id, team_id, start_date)
);

CREATE TABLE brand (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE,
    manufacturer text,
    tier smallint CHECK (tier BETWEEN 1 AND 5),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE card_set (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id uuid NOT NULL REFERENCES brand(id),
    sport text NOT NULL DEFAULT 'basketball',
    season_start smallint NOT NULL,
    season_end smallint,
    product_name text NOT NULL,
    set_name text,
    release_date date,
    manufacturer_code text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (season_start BETWEEN 1940 AND 2200),
    CHECK (season_end IS NULL OR season_end BETWEEN season_start AND season_start + 1),
    UNIQUE (brand_id, season_start, product_name, set_name)
);

CREATE TABLE card_definition (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    card_set_id uuid NOT NULL REFERENCES card_set(id),
    player_id uuid NOT NULL REFERENCES player(id),
    card_number text,
    insert_name text,
    parallel_name text,
    is_rookie boolean NOT NULL DEFAULT false,
    is_autograph boolean NOT NULL DEFAULT false,
    is_patch boolean NOT NULL DEFAULT false,
    is_case_hit boolean NOT NULL DEFAULT false,
    print_run integer CHECK (print_run IS NULL OR print_run > 0),
    checklist_url text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX card_definition_identity_uidx ON card_definition (
    card_set_id,
    player_id,
    (COALESCE(card_number, '')),
    (COALESCE(insert_name, '')),
    (COALESCE(parallel_name, ''))
);

CREATE INDEX card_definition_player_idx ON card_definition (player_id);
CREATE INDEX card_definition_set_idx ON card_definition (card_set_id);

CREATE TABLE collection_item (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_user(id),
    card_definition_id uuid NOT NULL REFERENCES card_definition(id),
    serial_number integer,
    serial_total integer,
    grade_company text,
    grade_value numeric(4,1),
    grade_cert_number text,
    condition_note text,
    image_front_url text,
    image_back_url text,
    strategy_tags text[] NOT NULL DEFAULT '{}',
    acquired_at date,
    disposed_at date,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (serial_number IS NULL OR serial_number > 0),
    CHECK (serial_total IS NULL OR serial_total > 0),
    CHECK (serial_number IS NULL OR serial_total IS NULL OR serial_number <= serial_total),
    CHECK (grade_value IS NULL OR grade_value BETWEEN 0 AND 10),
    CHECK (disposed_at IS NULL OR acquired_at IS NULL OR disposed_at >= acquired_at)
);

CREATE INDEX collection_item_owner_idx ON collection_item (owner_id);
CREATE INDEX collection_item_definition_idx ON collection_item (card_definition_id);
CREATE INDEX collection_item_strategy_tags_idx ON collection_item USING gin (strategy_tags);

CREATE TABLE card_transaction (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_item_id uuid NOT NULL REFERENCES collection_item(id),
    transaction_type transaction_type NOT NULL,
    transacted_at timestamptz NOT NULL,
    quantity numeric(12,4) NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price numeric(18,2) NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
    fee_amount numeric(18,2) NOT NULL DEFAULT 0 CHECK (fee_amount >= 0),
    currency char(3) NOT NULL,
    fx_rate_to_base numeric(18,8) NOT NULL DEFAULT 1 CHECK (fx_rate_to_base > 0),
    venue text,
    external_order_id text,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX card_transaction_item_time_idx
    ON card_transaction (collection_item_id, transacted_at DESC);

CREATE TABLE data_source (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    source_type text NOT NULL,
    base_url text,
    active boolean NOT NULL DEFAULT true,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE market_comp (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    card_definition_id uuid NOT NULL REFERENCES card_definition(id),
    data_source_id uuid REFERENCES data_source(id),
    occurred_at timestamptz NOT NULL,
    sale_price numeric(18,2) NOT NULL CHECK (sale_price >= 0),
    shipping_price numeric(18,2) NOT NULL DEFAULT 0 CHECK (shipping_price >= 0),
    currency char(3) NOT NULL,
    fx_rate_to_cny numeric(18,8),
    grade_company text,
    grade_value numeric(4,1),
    serial_number integer,
    listing_title text,
    source_url text,
    external_id text,
    match_confidence numeric(5,4) CHECK (match_confidence BETWEEN 0 AND 1),
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (data_source_id, external_id)
);

CREATE INDEX market_comp_definition_time_idx
    ON market_comp (card_definition_id, occurred_at DESC);

CREATE TABLE valuation_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_item_id uuid NOT NULL REFERENCES collection_item(id),
    valued_at timestamptz NOT NULL DEFAULT now(),
    method valuation_method NOT NULL,
    currency char(3) NOT NULL,
    low_value numeric(18,2) NOT NULL CHECK (low_value >= 0),
    fair_value numeric(18,2) NOT NULL CHECK (fair_value >= 0),
    high_value numeric(18,2) NOT NULL CHECK (high_value >= 0),
    confidence numeric(5,4) CHECK (confidence BETWEEN 0 AND 1),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    model_version text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (low_value <= fair_value AND fair_value <= high_value)
);

CREATE INDEX valuation_item_time_idx
    ON valuation_snapshot (collection_item_id, valued_at DESC);

CREATE TABLE player_metric (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id uuid NOT NULL REFERENCES player(id),
    data_source_id uuid REFERENCES data_source(id),
    season text NOT NULL,
    scope text NOT NULL DEFAULT 'season',
    scope_date date,
    metrics jsonb NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (player_id, data_source_id, season, scope, scope_date)
);

CREATE INDEX player_metric_player_season_idx ON player_metric (player_id, season);

CREATE TABLE player_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id uuid NOT NULL REFERENCES player(id),
    team_id uuid REFERENCES team(id),
    data_source_id uuid REFERENCES data_source(id),
    event_type player_event_type NOT NULL,
    occurred_at timestamptz NOT NULL,
    headline text NOT NULL,
    summary text,
    sentiment numeric(5,4) CHECK (sentiment BETWEEN -1 AND 1),
    impact_score numeric(5,2) CHECK (impact_score BETWEEN 0 AND 100),
    source_url text,
    external_id text,
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (data_source_id, external_id)
);

CREATE INDEX player_event_player_time_idx ON player_event (player_id, occurred_at DESC);

CREATE TABLE scoring_model (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    version text NOT NULL,
    description text,
    weights jsonb NOT NULL,
    thresholds jsonb NOT NULL DEFAULT '{}'::jsonb,
    active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

CREATE UNIQUE INDEX one_active_scoring_model_idx
    ON scoring_model (name) WHERE active;

CREATE TABLE card_score (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_item_id uuid NOT NULL REFERENCES collection_item(id),
    scoring_model_id uuid NOT NULL REFERENCES scoring_model(id),
    scored_at timestamptz NOT NULL DEFAULT now(),
    total_score numeric(5,2) NOT NULL CHECK (total_score BETWEEN 0 AND 100),
    factor_scores jsonb NOT NULL,
    factor_inputs jsonb NOT NULL DEFAULT '{}'::jsonb,
    explanation jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (collection_item_id, scoring_model_id, scored_at)
);

CREATE INDEX card_score_item_time_idx ON card_score (collection_item_id, scored_at DESC);

CREATE TABLE analysis_request (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_user(id),
    collection_item_id uuid REFERENCES collection_item(id),
    status analysis_status NOT NULL DEFAULT 'pending',
    input_payload jsonb NOT NULL,
    evidence_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    requested_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    error_message text,
    CHECK (completed_at IS NULL OR completed_at >= requested_at)
);

CREATE TABLE recommendation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_request_id uuid NOT NULL UNIQUE REFERENCES analysis_request(id),
    card_score_id uuid REFERENCES card_score(id),
    rating recommendation_rating NOT NULL,
    currency char(3) NOT NULL,
    fair_value_low numeric(18,2) NOT NULL CHECK (fair_value_low >= 0),
    fair_value_high numeric(18,2) NOT NULL CHECK (fair_value_high >= fair_value_low),
    target_sell_low numeric(18,2) NOT NULL CHECK (target_sell_low >= 0),
    target_sell_high numeric(18,2) NOT NULL CHECK (target_sell_high >= target_sell_low),
    holding_period_min_months integer NOT NULL CHECK (holding_period_min_months >= 0),
    holding_period_max_months integer NOT NULL CHECK (holding_period_max_months >= holding_period_min_months),
    confidence numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    thesis text NOT NULL,
    catalysts jsonb NOT NULL DEFAULT '[]'::jsonb,
    risks jsonb NOT NULL DEFAULT '[]'::jsonb,
    model_provider text,
    model_name text,
    prompt_version text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE VIEW latest_item_valuation AS
SELECT DISTINCT ON (collection_item_id)
    collection_item_id,
    valued_at,
    method,
    currency,
    low_value,
    fair_value,
    high_value,
    confidence
FROM valuation_snapshot
ORDER BY collection_item_id, valued_at DESC, created_at DESC;

CREATE VIEW item_cash_ledger AS
SELECT
    collection_item_id,
    SUM(CASE
        WHEN transaction_type = 'buy' THEN quantity
        WHEN transaction_type = 'sell' THEN -quantity
        ELSE 0
    END) AS quantity_held,
    SUM(CASE
        WHEN transaction_type = 'buy' THEN (quantity * unit_price + fee_amount) * fx_rate_to_base
        WHEN transaction_type = 'sell' THEN -(quantity * unit_price - fee_amount) * fx_rate_to_base
        WHEN transaction_type = 'fee' THEN fee_amount * fx_rate_to_base
        ELSE quantity * unit_price * fx_rate_to_base
    END) AS net_cash_invested_base
FROM card_transaction
GROUP BY collection_item_id;

COMMIT;
