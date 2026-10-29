-- Unknown physical card identity remains NULL, while the owned copy and photos exist.
ALTER TABLE player ALTER COLUMN full_name DROP NOT NULL,
 ALTER COLUMN normalized_name DROP NOT NULL;
ALTER TABLE brand ALTER COLUMN name DROP NOT NULL;
ALTER TABLE card_set ALTER COLUMN season_start DROP NOT NULL,
 ALTER COLUMN product_name DROP NOT NULL;
