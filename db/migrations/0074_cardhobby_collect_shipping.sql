BEGIN;
ALTER TABLE cardhobby_account ADD COLUMN IF NOT EXISTS collect_shipping_default numeric(18,2)
 CHECK (collect_shipping_default >= 0);
COMMENT ON COLUMN cardhobby_account.collect_shipping_default IS
 'Owner-authorized per-order SF collect shipping estimate, not marketplace paid shipping; NULL disables default.';
COMMIT;
