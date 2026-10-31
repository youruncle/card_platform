"""The public transport must not leak unfinished business capabilities."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


def transport_client():
    from card_platform.mcp.server import create_mcp_server

    server = create_mcp_server()

    @asynccontextmanager
    async def lifespan(app):
        async with server.session_manager.run():
            yield

    host = FastAPI(lifespan=lifespan)
    host.mount("/", server.streamable_http_app())
    return TestClient(host, base_url="http://localhost")


HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}


def rpc(client, method, params=None):
    return client.post("/mcp", headers=HEADERS, json={
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params or {},
    })


def test_initialization_exposes_transport_without_unimplemented_tools():
    with transport_client() as client:
        response = rpc(client, "initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "card-manager-test", "version": "1"},
        })
        assert response.status_code == 200
        assert response.json()["result"]["serverInfo"]["name"] == "Card Manager"
        assert rpc(client, "tools/list").json()["result"]["tools"] == []
        assert client.post("/mcp/mcp", headers=HEADERS, json={}).status_code == 404


def test_unknown_tool_is_an_error_without_traceback_or_internal_paths():
    with transport_client() as client:
        response = rpc(client, "tools/call", {"name": "permanent_delete", "arguments": {}})
        result = response.json()
        assert result.get("error") or result.get("result", {}).get("isError")
        assert "Traceback" not in response.text
        assert "/Users/" not in response.text


def test_transport_can_restart_after_lifespan_cleanup():
    for _ in range(2):
        with transport_client() as client:
            assert rpc(client, "tools/list").status_code == 200


def test_transport_rejects_dns_rebinding_host():
    with transport_client() as client:
        response = client.post("/mcp", headers={**HEADERS, "Host": "attacker.invalid"}, json={})
        assert response.status_code == 421


def test_error_contract_rejects_unrecognized_fields():
    from pydantic import ValidationError
    from card_platform.mcp.schemas import McpError

    assert McpError(code="INVALID_INPUT", message="请选择卡片", recovery="查询编号").code == "INVALID_INPUT"
    with pytest.raises(ValidationError):
        McpError(code="INVALID_INPUT", message="bad", recovery="retry", owner_id="override")


def test_parent_api_lifespan_can_restart_without_reusing_sdk_session_manager(mcp_test_dsn, monkeypatch):
    import hashlib
    from uuid import uuid4
    import psycopg
    from card_platform.config import get_settings
    from card_platform.main import app

    token = f"isolated-test-{uuid4()}"
    with psycopg.connect(mcp_test_dsn) as conn:
        owner = conn.execute("INSERT INTO app_user(email,display_name) VALUES (%s,'Parent runtime') RETURNING id",
                             (f"parent-{uuid4()}@example.invalid",)).fetchone()[0]
        cid = str(uuid4())
        conn.execute("INSERT INTO chatgpt_oauth_client(client_id,metadata) VALUES (%s,'{}')", (cid,))
        grant = conn.execute("INSERT INTO chatgpt_connection(owner_id,client_id,scopes) VALUES (%s,%s,'{cards:read}') RETURNING id",
                             (owner, cid)).fetchone()[0]
        conn.execute("""INSERT INTO chatgpt_oauth_token(token_hash,connection_id,kind,issuer,resource,scopes,expires_at)
            VALUES (%s,%s,'access','https://cardsp.cn/v1/chatgpt/oauth','https://cardsp.cn/mcp',
            '{cards:read}',now()+interval '15 minutes')""", (hashlib.sha256(token.encode()).hexdigest(), grant))
    monkeypatch.setenv("DATABASE_URL", mcp_test_dsn)
    for name in ("VALUATION", "RECOGNITION", "SCANNER", "PURCHASE_COST", "PLAYER_PRICE"):
        monkeypatch.setenv(f"{name}_WORKER_ENABLED", "false")
    get_settings.cache_clear()
    try:
        for _ in range(2):
            with TestClient(app, base_url="https://cardsp.cn") as client:
                assert client.get("/health/ready").status_code == 200
                assert client.post("/mcp", headers=HEADERS, json={}).status_code == 401
                metadata = client.get("/.well-known/oauth-protected-resource/mcp")
                assert metadata.status_code == 200
                assert metadata.json()["resource"] == "https://cardsp.cn/mcp"
                issuer = client.get("/.well-known/oauth-authorization-server/v1/chatgpt/oauth")
                assert issuer.status_code == 200
                assert issuer.json()["token_endpoint_auth_methods_supported"] == ["none"]
                headers = {**HEADERS, "Authorization": f"Bearer {token}"}
                tools = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1,
                    "method": "tools/list", "params": {}}).json()["result"]["tools"]
                assert {tool["name"] for tool in tools} == {"get_profile", "get_capabilities", "search_cards",
                    "get_card_detail", "get_portfolio_summary", "get_card_image", "prepare_card_import",
                    "get_job_status", "prepare_capture_confirmation", "execute_operation", "prepare_card_change",
                    "prepare_transaction", "prepare_trash_change", "get_activity_report", "start_card_research", "get_research_result",
                    "prepare_research_publication", "create_batch", "list_jobs", "retry_job",
                    "start_purchase_cost_lookup", "get_purchase_cost_lookup", "list_purchase_cost_lookups"}
                importer = next(tool for tool in tools if tool["name"] == "prepare_card_import")
                assert importer["_meta"]["openai/fileParams"] == ["files"]
                assert importer["annotations"]["readOnlyHint"] is False
                assert "files" in importer["inputSchema"]["properties"]
                response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2,
                    "method": "tools/call", "params": {"name": "search_cards", "arguments": {}}})
                assert response.json()["result"]["structuredContent"]["items"] == []
    finally:
        get_settings.cache_clear()


def test_transport_accepts_fixed_internal_reverse_proxy_host():
    with transport_client() as client:
        response=client.post('/mcp',headers={**HEADERS,'Host':'api:8000'},json={
            'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}})
        assert response.status_code==200
