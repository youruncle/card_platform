BEGIN;

CREATE TABLE card_identity_correction (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
    field_name text NOT NULL CHECK (field_name IN (
        'card_number', 'insert_name', 'parallel_name', 'serial_total',
        'is_autograph', 'is_patch'
    )),
    current_value jsonb,
    suggested_value jsonb NOT NULL,
    confidence numeric(5, 4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    detector_version text NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (
        status IN ('open', 'applied', 'superseded', 'dismissed')
    ),
    detected_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz,
    applied_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX card_identity_correction_open_field_uidx
    ON card_identity_correction (collection_item_id, field_name)
    WHERE status = 'open';
CREATE INDEX card_identity_correction_item_status_idx
    ON card_identity_correction (collection_item_id, status, detected_at DESC);

COMMIT;
