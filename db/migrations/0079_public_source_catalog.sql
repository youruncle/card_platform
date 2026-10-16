BEGIN;

-- Public observations have no owner, identity binding, acceptance or valuation.
CREATE TABLE public_source_catalog (
    provider_code text NOT NULL CHECK (provider_code = 'cardhobby'),
    external_sale_id text NOT NULL,
    source_url text NOT NULL,
    image_url text NOT NULL,
    observation jsonb NOT NULL,
    captured_at timestamptz NOT NULL,
    PRIMARY KEY (provider_code, external_sale_id)
);

COMMIT;
