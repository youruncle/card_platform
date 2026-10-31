import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount

from test_mcp_auth import exchange_params, new_grant, oauth_client

pytestmark = pytest.mark.asyncio


async def test_capabilities_profile_scopes_and_revocation_over_real_mcp(mcp_database):
    from card_platform.mcp.server import create_mcp_server
    provider, oauth = await oauth_client(mcp_database)
    async with oauth:
        cid, code, owner, _ = await new_grant(mcp_database, provider, oauth)
        tokens = (await oauth.post("/token", data=exchange_params(provider, cid, code))).json()
        server = create_mcp_server(provider)
        app = Starlette(routes=[Mount("/", server.streamable_http_app())])
        async with server.session_manager.run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://cardsp.cn") as client:
                headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
                query = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
                assert (await client.post("/mcp", headers=headers, json=query)).status_code == 401
                headers["Authorization"] = f"Bearer {tokens['access_token']}"
                result = (await client.post("/mcp", headers=headers, json=query)).json()["result"]
                names = {t["name"] for t in result["tools"]}
                assert names == {"get_capabilities", "get_profile"}
                assert all(t["annotations"]["readOnlyHint"] for t in result["tools"])

                async def call(name, arguments=None):
                    response = await client.post("/mcp", headers=headers, json={
                        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments or {}},
                    })
                    assert response.status_code == 200, response.text
                    return response.json()["result"]

                capabilities = (await call("get_capabilities"))["structuredContent"]
                assert set(capabilities["enabled_tools"]) == names
                assert "prepare_transaction" not in capabilities["enabled_tools"]
                profile = (await call("get_profile"))["structuredContent"]
                assert profile["owner_id"] == str(owner)
                assert profile["display_name"] == "MCP test"
                assert profile["scopes"] == ["cards:read", "cards:write"]
                rejected = await call("get_profile", {"owner_id": str(owner)})
                assert rejected["isError"] is True
                assert rejected["structuredContent"]["code"] == "INVALID_INPUT"
                assert tokens["access_token"] not in json.dumps(rejected)
                assert "/Users/" not in json.dumps(rejected)
                await oauth.post("/revoke", data={"client_id": cid, "token": tokens["access_token"]})
                assert (await client.post("/mcp", headers=headers, json=query)).status_code == 401
