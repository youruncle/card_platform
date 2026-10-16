BEGIN;
ALTER TABLE cardhobby_purchase_unit ADD COLUMN bundle_index integer NOT NULL DEFAULT 0 CHECK(bundle_index BETWEEN 0 AND 20);
ALTER TABLE cardhobby_purchase_unit ADD COLUMN bundle_evidence jsonb NOT NULL DEFAULT '{}';
ALTER TABLE cardhobby_purchase_unit DROP CONSTRAINT cardhobby_purchase_unit_order_id_item_id_unit_index_key;
ALTER TABLE cardhobby_purchase_unit ADD UNIQUE(order_id,item_id,unit_index,bundle_index);
COMMIT;
