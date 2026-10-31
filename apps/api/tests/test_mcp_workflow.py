"""Six actual transport/approval operations against a disposable owner's cards."""
import asyncio
import hashlib
from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from card_platform.auth import AuthenticatedUser, current_user
from test_mcp_mutations import mutation_case as shared_mutation_case, transaction

mutation_case = shared_mutation_case
pytestmark = pytest.mark.asyncio


async def test_six_operations_through_authenticated_mcp_and_browser_approval(mutation_case, monkeypatch):
    from card_platform.mcp.server import create_authenticated_runtime
    from card_platform.mcp.operation_api import router

    tools, principal, ids = mutation_case
    token = f"workflow-isolated-{uuid4()}"
    async with tools.database.connection() as conn:
        await conn.execute("""INSERT INTO chatgpt_oauth_token(token_hash,connection_id,kind,issuer,resource,scopes,expires_at)
          VALUES (%s,%s,'access','https://cardsp.cn/v1/chatgpt/oauth','https://cardsp.cn/mcp',%s,now()+interval '15 minutes')""",
          (hashlib.sha256(token.encode()).hexdigest(), principal.connection_id, list(principal.scopes)))
    # Same factory and approval router as production; no substituted business handlers.
    server, transport = create_authenticated_runtime(tools.database)
    monkeypatch.setattr(server.card_tools.provider, "database", tools.database)
    app = FastAPI()
    app.state.chatgpt_operations = server.capture_tools.operations
    app.include_router(router)
    app.mount('/', transport)
    app.dependency_overrides[current_user] = lambda: AuthenticatedUser(id=principal.owner_id, session_id=uuid4(),
        email="workflow@example.invalid", display_name="Workflow", base_currency="CNY", role="member")
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25", "Authorization": f"Bearer {token}"}
    async with server.session_manager.run(), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cardsp.cn") as client:
        async def call(name, arguments):
            response = await client.post('/mcp', headers=headers, json={"jsonrpc": "2.0", "id": 1,
                "method": "tools/call", "params": {"name": name, "arguments": arguments}})
            assert response.status_code == 200, response.text
            result = response.json()['result']
            assert not result.get('isError'), result
            return result['structuredContent']

        steps = [('prepare_transaction', {'transaction': transaction(transaction_type='buy', unit_price='200', fee_amount='0', fee_direction='none', transacted_at='2026-10-02T00:00:00+08:00')}),
                 ('prepare_transaction', {'transaction': transaction(quantity='2', unit_price='250')}),
                 ('prepare_transaction', {'transaction': transaction(transaction_type='fee', unit_price='0', fee_amount='5', transacted_at='2026-10-11T00:00:00+08:00')}),
                 ('prepare_card_change', {'changes': {'notes': 'Six-step transport acceptance'}}),
                 ('prepare_trash_change', {'action': 'trash'}), ('prepare_trash_change', {'action': 'restore'})]
        for name, fields in steps:
            async with tools.database.connection() as conn:
                from card_platform.mcp.portfolio import card_revision
                revision = await card_revision(conn, principal.owner_id, ids[0], include_deleted=True)
            operation = await call(name, {'item_id': str(ids[0]), 'expected_revision': revision, 'idempotency_key': str(uuid4()), **fields})
            path = f"/v1/chatgpt/operations/{operation['operation_id']}"
            preview = await client.get(path)
            assert preview.status_code == 200
            approved = await client.post(path+'/approve', headers={'Origin': 'https://cardsp.cn'}, json={'payload_hash': operation['payload_hash']})
            assert approved.status_code == 200, approved.text
            executed = await asyncio.gather(*(call('execute_operation', {'operation_id': operation['operation_id']}) for _ in range(3)))
            assert sum(not result['already_executed'] for result in executed) == 1
        result = await call('get_card_detail', {'item_id': str(ids[0])})
        assert str(result['item']['quantity_held']) == '0'
        assert Decimal(result['item']['realized_profit']) == Decimal('185')
        async with tools.database.connection() as conn:
            row = await (await conn.execute('SELECT notes,deleted_at,holding_status FROM collection_item WHERE id=%s', (ids[0],))).fetchone()
            assert row == {'notes': 'Six-step transport acceptance', 'deleted_at': None, 'holding_status': 'sold'}
            assert (await (await conn.execute('SELECT count(*) AS n FROM card_transaction WHERE collection_item_id=%s', (ids[0],))).fetchone())['n'] == 4
