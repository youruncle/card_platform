"""Opt-in disposable database for ChatGPT integration tests.

MCP_TEST_DATABASE_URL is intentionally separate from the normal application DSN.
Only a database whose name starts with manager_test_ may be initialized here.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def mcp_test_dsn():
    dsn = os.environ.get("MCP_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires disposable MCP_TEST_DATABASE_URL")
    if not urlsplit(dsn).path.lstrip("/").startswith("manager_test_"):
        pytest.fail("MCP tests require a database named manager_test_*")
    migrations = Path(__file__).resolve().parents[3] / "db/migrations"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS mcp_test_migration(name text PRIMARY KEY)")
        for path in sorted(migrations.glob("*.sql")):
            if not conn.execute(
                "SELECT 1 FROM mcp_test_migration WHERE name=%s", (path.name,)
            ).fetchone():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO mcp_test_migration VALUES (%s)", (path.name,))
    return dsn


@pytest_asyncio.fixture
async def mcp_database(mcp_test_dsn):
    from card_platform.config import Settings
    from card_platform.db import Database

    database = Database(Settings(database_url=mcp_test_dsn))
    await database.open()
    yield database
    await database.close()


@pytest_asyncio.fixture
async def mcp_principal(mcp_database):
    from uuid import uuid4
    from card_platform.mcp.schemas import McpPrincipal

    async with mcp_database.connection() as conn:
        owner = (await (await conn.execute(
            "INSERT INTO app_user(email,display_name) VALUES (%s,'Manager integration') RETURNING id",
            (f"manager-{uuid4()}@example.invalid",),
        )).fetchone())["id"]
        client_id = str(uuid4())
        await conn.execute("INSERT INTO chatgpt_oauth_client(client_id,metadata) VALUES (%s,'{}')", (client_id,))
        scopes = ["cards:read", "cards:write", "transactions:write", "research:run"]
        grant = (await (await conn.execute("""INSERT INTO chatgpt_connection(owner_id,client_id,scopes)
                         VALUES (%s,%s,%s) RETURNING id""", (owner, client_id, scopes))).fetchone())["id"]
    return McpPrincipal(owner_id=owner, connection_id=grant, scopes=scopes)
