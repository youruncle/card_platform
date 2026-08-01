BEGIN;

ALTER TABLE card_definition
    ADD COLUMN has_inscription boolean NOT NULL DEFAULT false,
    ADD COLUMN inscription_text text;

ALTER TABLE card_definition
    ADD CONSTRAINT card_definition_inscription_requires_autograph
    CHECK (NOT has_inscription OR is_autograph);

COMMIT;
