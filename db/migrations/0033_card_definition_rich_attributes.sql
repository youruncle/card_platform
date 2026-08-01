BEGIN;

ALTER TABLE card_definition
    ADD COLUMN autograph_type text NOT NULL DEFAULT 'none'
        CHECK (autograph_type IN ('on_card', 'sticker', 'cut', 'unknown', 'none')),
    ADD COLUMN is_memorabilia boolean NOT NULL DEFAULT false,
    ADD COLUMN memorabilia_types text[] NOT NULL DEFAULT '{}',
    ADD COLUMN memorabilia_usage text NOT NULL DEFAULT 'none'
        CHECK (
            memorabilia_usage IN (
                'game_used',
                'player_worn',
                'event_worn',
                'not_from_specific_game_or_event',
                'manufactured',
                'unknown',
                'none'
            )
        ),
    ADD COLUMN memorabilia_player text;

UPDATE card_definition
SET is_memorabilia = true,
    memorabilia_types = ARRAY['patch'],
    memorabilia_usage = 'unknown'
WHERE is_patch;

COMMIT;
