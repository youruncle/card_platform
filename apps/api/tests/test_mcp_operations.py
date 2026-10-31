import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from card_platform.mcp.schemas import McpFailure, McpPrincipal

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def operation_case(mcp_database):
    from card_platform.mcp.operations import OperationHandler, OperationService

    async with mcp_database.connection() as conn:
        owner = (await (await conn.execute(
            "INSERT INTO app_user(email,display_name) VALUES (%s,'Operation test') RETURNING id",
            (f"operation-{uuid4()}@example.invalid",),
        )).fetchone())["id"]
        client_id = str(uuid4())
        await conn.execute("INSERT INTO chatgpt_oauth_client(client_id,metadata) VALUES (%s,'{}')", (client_id,))
        grant = (await (await conn.execute(
            "INSERT INTO chatgpt_connection(owner_id,client_id,scopes) VALUES (%s,%s,'{cards:read,cards:write}') RETURNING id",
            (owner, client_id),
        )).fetchone())["id"]
        await conn.execute("""CREATE TABLE IF NOT EXISTS mcp_test_target(
            id uuid PRIMARY KEY,owner_id uuid NOT NULL,value integer NOT NULL,revision integer NOT NULL)""")
        target_id = uuid4()
        await conn.execute("INSERT INTO mcp_test_target VALUES (%s,%s,0,1)", (target_id, owner))

    async def snapshot(conn, principal, payload):
        row = await (await conn.execute(
            "SELECT revision FROM mcp_test_target WHERE id=%s AND owner_id=%s FOR UPDATE",
            (UUID(payload["target_id"]), principal.owner_id),
        )).fetchone()
        if not row:
            raise McpFailure("NOT_FOUND", "目标不存在")
        return f"r{row['revision']}"

    async def execute(conn, principal, payload):
        row = await (await conn.execute(
            """UPDATE mcp_test_target SET value=value+%s,revision=revision+1
               WHERE id=%s AND owner_id=%s RETURNING id,value,revision""",
            (payload["amount"], UUID(payload["target_id"]), principal.owner_id),
        )).fetchone()
        if payload.get("fail"):
            raise McpFailure("INVALID_INPUT", "业务失败")
        return {"target_id": str(row["id"]), "value": row["value"], "revision": f"r{row['revision']}"}

    service = OperationService(mcp_database, "https://cardsp.cn")
    service.register("update", OperationHandler(frozenset({"cards:write"}), snapshot, execute))
    principal = McpPrincipal(owner_id=owner, connection_id=grant, scopes={"cards:read", "cards:write"})
    return service, principal, {"target_id": str(target_id), "amount": 10}


async def prepare(case, key=None, **extra):
    service, principal, payload = case
    return await service.prepare(principal, "update", key or uuid4(), {**payload, **extra}, "r1")


async def test_same_key_returns_original_but_changed_parameters_conflict(operation_case):
    service, principal, payload = operation_case
    key = uuid4()
    first = await prepare(operation_case, key)
    second = await prepare(operation_case, key)
    assert first["operation_id"] == second["operation_id"]
    assert first["payload"] == payload
    expires = datetime.fromisoformat(first["expires_at"])
    assert 895 < (expires - datetime.now(timezone.utc)).total_seconds() <= 900
    with pytest.raises(McpFailure) as error:
        await prepare(operation_case, key, amount=11)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


async def test_other_owner_or_other_connection_cannot_reuse_operation(operation_case):
    service, principal, _ = operation_case
    operation = await prepare(operation_case)
    for changed in (principal.model_copy(update={"owner_id": uuid4()}),
                    principal.model_copy(update={"connection_id": uuid4()})):
        with pytest.raises(McpFailure):
            await service.lookup(changed, operation["operation_id"])


async def test_expired_preview_cannot_be_approved(operation_case):
    service, principal, _ = operation_case
    operation = await prepare(operation_case)
    async with service.database.connection() as conn:
        await conn.execute("UPDATE chatgpt_operation SET expires_at=now()-interval '1 second' WHERE id=%s",
                           (UUID(operation["operation_id"]),))
    with pytest.raises(McpFailure) as error:
        await service.approve(principal.owner_id, uuid4(), operation["operation_id"], operation["payload_hash"])
    assert error.value.code == "EXPIRED_OPERATION"


async def test_approval_cannot_accept_changed_payload_or_model_confirmation(operation_case):
    service, principal, _ = operation_case
    operation = await prepare(operation_case)
    with pytest.raises(McpFailure) as error:
        await service.execute(principal, operation["operation_id"])
    assert error.value.code == "CONFIRMATION_REQUIRED"
    with pytest.raises(McpFailure):
        await service.approve(principal.owner_id, uuid4(), operation["operation_id"], "f" * 64)
    async with service.database.connection() as conn:
        await conn.execute("UPDATE chatgpt_operation SET payload='{}' WHERE id=%s", (UUID(operation["operation_id"]),))
    with pytest.raises(McpFailure):
        await service.approve(principal.owner_id, uuid4(), operation["operation_id"], operation["payload_hash"])


async def test_concurrent_execute_only_one_effect_and_success_survives_expiry(operation_case):
    service, principal, _ = operation_case
    operation = await prepare(operation_case)
    await service.approve(principal.owner_id, uuid4(), operation["operation_id"], operation["payload_hash"])
    results = await asyncio.gather(*(service.execute(principal, operation["operation_id"]) for _ in range(3)))
    assert all(result["readback"]["value"] == 10 for result in results)
    assert sum(not result["already_executed"] for result in results) == 1
    async with service.database.connection() as conn:
        await conn.execute("UPDATE chatgpt_operation SET expires_at=now()-interval '1 hour' WHERE id=%s",
                           (UUID(operation["operation_id"]),))
    result = await service.execute(principal, operation["operation_id"])
    assert result["already_executed"] is True
    assert result["readback"]["value"] == 10


async def test_business_error_rolls_back_effect(operation_case):
    service, principal, _ = operation_case
    operation = await prepare(operation_case, fail=True)
    await service.approve(principal.owner_id, uuid4(), operation["operation_id"], operation["payload_hash"])
    with pytest.raises(McpFailure):
        await service.execute(principal, operation["operation_id"])
    async with service.database.connection() as conn:
        row = await (await conn.execute("SELECT value,revision FROM mcp_test_target WHERE id=%s",
                                       (UUID(operation["payload"]["target_id"]),))).fetchone()
    assert row == {"value": 0, "revision": 1}


async def test_changed_revision_or_scope_after_approval_blocks_write(operation_case):
    service, principal, payload = operation_case
    operation = await prepare(operation_case)
    await service.approve(principal.owner_id, uuid4(), operation["operation_id"], operation["payload_hash"])
    async with service.database.connection() as conn:
        await conn.execute("UPDATE mcp_test_target SET revision=2 WHERE id=%s", (UUID(payload["target_id"]),))
    with pytest.raises(McpFailure) as error:
        await service.execute(principal, operation["operation_id"])
    assert error.value.code == "REVISION_CONFLICT"
    async with service.database.connection() as conn:
        await conn.execute("UPDATE chatgpt_connection SET scopes='{cards:read}' WHERE id=%s", (principal.connection_id,))
    with pytest.raises(McpFailure) as error:
        await service.execute(principal, operation["operation_id"])
    assert error.value.code == "FORBIDDEN"
