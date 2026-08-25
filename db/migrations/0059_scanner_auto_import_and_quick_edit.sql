ALTER TABLE collection_item
    ADD COLUMN IF NOT EXISTS is_numbered boolean NOT NULL DEFAULT false;

UPDATE collection_item ci
SET is_numbered = true
FROM card_definition cd
WHERE cd.id = ci.card_definition_id
  AND (ci.serial_number IS NOT NULL OR ci.serial_total IS NOT NULL OR cd.print_run IS NOT NULL)
  AND ci.is_numbered = false;

ALTER TABLE scanner_recognition_job
    ADD COLUMN IF NOT EXISTS collection_item_id uuid
        REFERENCES collection_item(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS imported_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS uq_scanner_recognition_job_collection_item
    ON scanner_recognition_job (collection_item_id)
    WHERE collection_item_id IS NOT NULL;
