BEGIN;

-- Discovery membership carries no identity acceptance or valuation authority.
-- A public sale remains globally unique in market_evidence.
CREATE TABLE market_evidence_discovery (
    identity_fingerprint text NOT NULL REFERENCES market_identity_cache(identity_fingerprint) ON DELETE CASCADE,
    market_evidence_id uuid NOT NULL REFERENCES market_evidence(id) ON DELETE CASCADE,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (identity_fingerprint, market_evidence_id)
);
CREATE INDEX market_evidence_discovery_source_idx ON market_evidence_discovery(market_evidence_id);

-- Backfill only associations actually present, not guessed historical matches.
INSERT INTO market_evidence_discovery(identity_fingerprint, market_evidence_id)
SELECT identity_fingerprint, id FROM market_evidence;

CREATE VIEW market_evidence_discovery_scope AS
SELECT identity_fingerprint, market_evidence_id FROM market_evidence_discovery
UNION
SELECT identity_fingerprint, id AS market_evidence_id FROM market_evidence;

COMMIT;
