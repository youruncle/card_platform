"""Index coverage and natural query plans in an isolated database."""
import json
import os
from pathlib import Path

import psycopg
import pytest

from test_mobile_postgres import account
from test_mobile_postgres import client as client
from test_portfolio_performance_postgres import create

pytestmark = pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="requires disposable PostgreSQL")
MIGRATION = Path(__file__).resolve().parents[3] / "db/migrations/0080_portfolio_read_indexes.sql"
INDEXES = {
    "collection_item_owner_active_order_idx", "card_transaction_first_purchase_idx",
    "valuation_snapshot_compatible_latest_idx", "card_identity_version_rejected_parent_idx",
    "identity_research_case_item_latest_idx",
}


def test_portfolio_hot_query_indexes_exist_and_migration_is_repeatable(client):
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        found = {row[0] for row in db.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public'")}
        assert INDEXES <= found
        db.execute(MIGRATION.read_text())
        found_again = {row[0] for row in db.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public'")}
        assert INDEXES <= found_again


def test_hot_queries_use_ordered_partial_indexes_without_forcing_planner(client):
    owner, headers = account(client)
    item_id = create(client, owner, headers, 0)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("""INSERT INTO collection_item(owner_id,card_definition_id)
            SELECT owner_id,card_definition_id FROM collection_item CROSS JOIN generate_series(1,1199)
            WHERE id=%s""", (item_id,))
        db.execute("""INSERT INTO card_transaction(collection_item_id,transaction_type,transacted_at,quantity,unit_price,currency)
            SELECT %s,CASE WHEN g<=980 THEN 'sell'::transaction_type ELSE 'buy'::transaction_type END,
                now()-g*interval '1 second',1,1,'CNY' FROM generate_series(1,1000) g""", (item_id,))
        db.execute("""INSERT INTO valuation_snapshot(collection_item_id,method,currency,low_value,fair_value,high_value,identity_compatibility,valued_at)
            SELECT %s,'manual','CNY',1,1,1,CASE WHEN g<=980 THEN 'incompatible' ELSE 'compatible' END,
                now()-g*interval '1 second' FROM generate_series(1,1000) g""", (item_id,))
        queries = {
            "collection_item_owner_active_order_idx": ("SELECT id FROM collection_item WHERE owner_id=%s AND deleted_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 30", (owner["id"],)),
            "card_transaction_first_purchase_idx": ("SELECT unit_price FROM card_transaction WHERE collection_item_id=%s AND transaction_type='buy' ORDER BY transacted_at,created_at,id LIMIT 1", (item_id,)),
            "valuation_snapshot_compatible_latest_idx": ("SELECT id FROM valuation_snapshot WHERE collection_item_id=%s AND identity_compatibility<>'incompatible' ORDER BY valued_at DESC,created_at DESC,id DESC LIMIT 1", (item_id,)),
        }
        for table in ("collection_item", "card_transaction", "valuation_snapshot"):
            db.execute(f"ANALYZE {table}")
        after = {}
        for index, (sql, parameters) in queries.items():
            plan = db.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, parameters).fetchone()[0][0]
            assert index in json.dumps(plan), plan
            assert '"Node Type": "Sort"' not in json.dumps(plan), plan
            after[index] = plan["Execution Time"]
        # Drop only this migration's indexes, inside this disposable transaction.
        for index in INDEXES:
            db.execute(f"DROP INDEX {index}")
        before = {index: db.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params).fetchone()[0][0]["Execution Time"] for index, (sql, params) in queries.items()}
        db.execute(MIGRATION.read_text())
        print("INDEX_PLANS " + json.dumps({"cards": 1200, "rows_per_history": 1000, "before_ms": before, "after_ms": after}))
