from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from card_platform.auth import AuthenticatedUser, current_user
from test_mcp_operations import operation_case as shared_operation_case, prepare

operation_case = shared_operation_case

pytestmark = pytest.mark.asyncio


async def test_approval_requires_login_origin_owner_and_exact_summary(operation_case):
    from card_platform.mcp.operation_api import router

    service, principal, _ = operation_case
    operation = await prepare(operation_case)
    app = FastAPI()
    app.state.chatgpt_operations = service
    app.include_router(router)
    path = f"/v1/chatgpt/operations/{operation['operation_id']}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cardsp.cn") as client:
        assert (await client.get(path)).status_code == 401
        user = AuthenticatedUser(id=principal.owner_id, session_id=uuid4(), email="test@example.invalid",
                                 display_name="Tester", base_currency="CNY", role="member")
        app.dependency_overrides[current_user] = lambda: user
        response = await client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["payload"] == operation["payload"]
        body = {"payload_hash": operation["payload_hash"]}
        for origin in (None, "https://attacker.invalid"):
            response = await client.post(path + "/approve", json=body,
                                         headers={"Origin": origin} if origin else {})
            assert response.status_code == 403
        headers = {"Origin": "https://cardsp.cn"}
        assert (await client.post(path + "/approve", headers=headers,
                                 json={**body, "confirmed": True})).status_code == 422
        assert (await client.post(path + "/approve", headers=headers,
                                 json={"payload_hash": "0" * 64})).status_code == 409
        wrong_user = AuthenticatedUser(**{**user.__dict__, "id": uuid4()})
        app.dependency_overrides[current_user] = lambda: wrong_user
        assert (await client.get(path)).status_code == 404
        assert (await client.post(path + "/approve", headers=headers, json=body)).status_code == 404
        app.dependency_overrides[current_user] = lambda: user
        assert (await client.post(path + "/approve", headers=headers, json=body)).status_code == 200
        async with service.database.connection() as conn:
            await conn.execute("UPDATE chatgpt_connection SET revoked_at=now() WHERE id=%s", (principal.connection_id,))
        assert (await client.post(path + "/approve", headers=headers, json=body)).status_code == 401
