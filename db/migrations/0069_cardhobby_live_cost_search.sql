BEGIN;
ALTER TABLE cardhobby_cost_job ADD COLUMN source_mode text NOT NULL DEFAULT 'synced'
 CHECK (source_mode IN ('synced','browser_search'));
COMMIT;
