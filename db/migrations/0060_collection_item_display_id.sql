ALTER TABLE collection_item
    ADD COLUMN IF NOT EXISTS display_id bigint;

WITH numbered AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY owner_id
               ORDER BY created_at, id
           ) - 1 AS display_id
    FROM collection_item
)
UPDATE collection_item ci
SET display_id = numbered.display_id
FROM numbered
WHERE numbered.id = ci.id
  AND ci.display_id IS NULL;

ALTER TABLE collection_item
    ALTER COLUMN display_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_collection_item_owner_display_id
    ON collection_item (owner_id, display_id);

CREATE TABLE IF NOT EXISTS collection_item_display_counter (
    owner_id uuid PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
    next_display_id bigint NOT NULL CHECK (next_display_id >= 0)
);

INSERT INTO collection_item_display_counter (owner_id, next_display_id)
SELECT owner_id, COALESCE(MAX(display_id) + 1, 0)
FROM collection_item
GROUP BY owner_id
ON CONFLICT (owner_id) DO UPDATE
SET next_display_id = GREATEST(
    collection_item_display_counter.next_display_id,
    EXCLUDED.next_display_id
);

CREATE OR REPLACE FUNCTION assign_collection_item_display_id()
RETURNS trigger AS $$
BEGIN
    IF NEW.display_id IS NULL THEN
        INSERT INTO collection_item_display_counter (owner_id, next_display_id)
        VALUES (NEW.owner_id, 1)
        ON CONFLICT (owner_id) DO UPDATE
        SET next_display_id = collection_item_display_counter.next_display_id + 1
        RETURNING next_display_id - 1 INTO NEW.display_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS collection_item_display_id_trigger ON collection_item;
CREATE TRIGGER collection_item_display_id_trigger
BEFORE INSERT ON collection_item
FOR EACH ROW EXECUTE FUNCTION assign_collection_item_display_id();
