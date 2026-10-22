BEGIN;
CREATE TABLE player_price_evidence_revision (
    player_id uuid PRIMARY KEY REFERENCES player(id),
    policy text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE player_price_evidence_audit (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_id uuid NOT NULL REFERENCES player(id),
    policy text NOT NULL,
    initial_rebuild boolean NOT NULL,
    report_date date NOT NULL,
    before_state jsonb NOT NULL,
    admitted_sources jsonb NOT NULL,
    blocked_sources jsonb NOT NULL,
    after_fingerprint text NOT NULL,
    transaction_id bigint NOT NULL DEFAULT txid_current(),
    rolled_back_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX player_price_evidence_audit_player_idx ON player_price_evidence_audit(player_id,id);
COMMIT;
