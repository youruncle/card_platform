from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError


def test_principal_rejects_unknown_scope_and_non_uuid_owner():
    from card_platform.mcp.schemas import McpPrincipal

    for values in ({"owner_id": "not-a-uuid", "scopes": ["cards:read"]},
                   {"owner_id": uuid4(), "scopes": ["admin"]}):
        with pytest.raises(ValidationError):
            McpPrincipal(connection_id=uuid4(), **values)


def test_operation_database_rejects_owner_mismatch_and_duplicate_key(mcp_test_dsn):
    with psycopg.connect(mcp_test_dsn) as conn:
        owners = [conn.execute(
            "INSERT INTO app_user(email,display_name) VALUES (%s,'Storage test') RETURNING id",
            (f"storage-{uuid4()}@example.invalid",),
        ).fetchone()[0] for _ in range(2)]
        cid = f"client-{uuid4()}"
        conn.execute("INSERT INTO chatgpt_oauth_client VALUES (%s,'{}',now())", (cid,))
        grant = conn.execute(
            "INSERT INTO chatgpt_connection(owner_id,client_id,scopes) VALUES (%s,%s,'{cards:write}') RETURNING id",
            (owners[0], cid),
        ).fetchone()[0]
        key = uuid4()
        query = """INSERT INTO chatgpt_operation(owner_id,connection_id,kind,idempotency_key,
                   payload,payload_hash,expected_revision) VALUES (%s,%s,'update',%s,'{}',%s,'r1')"""
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute(query, (owners[1], grant, key, "a" * 64))
        conn.execute(query, (owners[0], grant, key, "a" * 64))
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute(query, (owners[0], grant, key, "a" * 64))
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE chatgpt_connection SET scopes='{admin}' WHERE id=%s", (grant,))
        conn.rollback()


def test_migration_is_repeatable_without_new_collection_master(mcp_test_dsn):
    from pathlib import Path
    migration = Path(__file__).resolve().parents[3] / "db/migrations/0088_chatgpt_manager.sql"
    with psycopg.connect(mcp_test_dsn, autocommit=True) as conn:
        before = conn.execute("SELECT count(*) FROM collection_item").fetchone()[0]
        conn.execute(migration.read_text())
        conn.execute(migration.read_text())
        assert conn.execute("SELECT count(*) FROM collection_item").fetchone()[0] == before
