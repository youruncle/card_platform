from uuid import uuid4

import pytest

from card_platform.mcp.schemas import McpFailure, McpPrincipal
from test_mcp_mutations import mutation_case as shared_mutation_case, approve_execute, revision

mutation_case = shared_mutation_case
pytestmark = pytest.mark.asyncio


async def test_trash_restore_preserves_ledger_and_three_retries_only_return_original(mutation_case):
    from card_platform.mcp.mutations import PrepareTrashChangeRequest
    tools, principal, ids = mutation_case
    original_revision = await revision(tools, principal, ids[0])
    request = PrepareTrashChangeRequest(item_id=ids[0], expected_revision=original_revision,
                                       action="trash", reason="integration test", idempotency_key=uuid4())
    operation = await tools.prepare_trash_change(principal, request)
    result = await approve_execute(tools, principal, operation)
    assert result["readback"]["deleted_at"] is not None
    for _ in range(3):
        assert (await tools.operations.execute(principal, operation["operation_id"]))["already_executed"] is True
        assert (await tools.prepare_trash_change(principal, request))["operation_id"] == operation["operation_id"]
    async with tools.database.connection() as conn:
        after = await (await conn.execute("SELECT count(*) AS n FROM card_transaction WHERE collection_item_id=%s", (ids[0],))).fetchone()
        from card_platform.mcp.portfolio import card_revision
        deleted_revision = await card_revision(conn, principal.owner_id, ids[0], include_deleted=True)
    assert after["n"] == 1
    restore = await tools.prepare_trash_change(principal, PrepareTrashChangeRequest(item_id=ids[0],
        expected_revision=deleted_revision, action="restore", idempotency_key=uuid4()))
    assert (await approve_execute(tools, principal, restore))["readback"]["deleted_at"] is None
    async with tools.database.connection() as conn:
        detail = await tools.repository.item_detail(conn, principal.owner_id, ids[0])
    assert len(detail.transactions) == 1


async def test_authenticated_other_account_cannot_prepare_trash_for_someone_else(mutation_case):
    from card_platform.mcp.mutations import PrepareTrashChangeRequest
    tools, principal, ids = mutation_case
    async with tools.database.connection() as conn:
        owner = (await (await conn.execute("INSERT INTO app_user(email,display_name) VALUES (%s,'Other account') RETURNING id",
                                          (f"other-{uuid4()}@example.invalid",))).fetchone())["id"]
        cid = str(uuid4())
        await conn.execute("INSERT INTO chatgpt_oauth_client(client_id,metadata) VALUES (%s,'{}')", (cid,))
        grant = (await (await conn.execute("INSERT INTO chatgpt_connection(owner_id,client_id,scopes) VALUES (%s,%s,'{cards:read,cards:write}') RETURNING id",
                                            (owner, cid))).fetchone())["id"]
    other = McpPrincipal(owner_id=owner, connection_id=grant, scopes={"cards:read", "cards:write"})
    with pytest.raises(McpFailure) as error:
        await tools.prepare_trash_change(other, PrepareTrashChangeRequest(item_id=ids[0],
            expected_revision=await revision(tools, principal, ids[0]), action="trash", idempotency_key=uuid4()))
    assert error.value.code == "NOT_FOUND"


async def test_permanent_delete_is_not_a_valid_trash_action():
    from pydantic import ValidationError
    from card_platform.mcp.mutations import PrepareTrashChangeRequest
    with pytest.raises(ValidationError):
        PrepareTrashChangeRequest(item_id=uuid4(), expected_revision="r1", action="permanent_delete", idempotency_key=uuid4())
