"""Exercise the actual OAuth protocol handlers and persistent grant lifecycle."""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.asyncio

REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
VERIFIER = "test-verifier-" + "x" * 48
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


async def oauth_client(database):
    from card_platform.mcp.auth import OAuthConfig, CardOAuthProvider
    from card_platform.mcp.oauth_api import create_oauth_app

    config = OAuthConfig(redirect_uris=(REDIRECT,))
    provider = CardOAuthProvider(database, config)
    from starlette.applications import Starlette
    from starlette.routing import Mount
    host = Starlette(routes=[Mount(urlsplit(config.issuer).path, create_oauth_app(provider))])
    transport = httpx.ASGITransport(app=host)
    return provider, httpx.AsyncClient(transport=transport, base_url=config.issuer)


async def new_grant(database, provider, client):
    registered = await client.post("/register", json={
        "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "scope": "cards:read cards:write", "client_name": "Integration test",
    })
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]
    params = {"client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code",
              "code_challenge": CHALLENGE, "code_challenge_method": "S256",
              "state": "client-state", "resource": provider.config.resource,
              "scope": "cards:read cards:write"}
    response = await client.get("/authorize", params=params)
    assert response.status_code == 302, response.text
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["request_id"][0]
    async with database.connection() as conn:
        owner = (await (await conn.execute(
            "INSERT INTO app_user(email,display_name) VALUES (%s,'MCP test') RETURNING id",
            (f"mcp-{uuid4()}@example.invalid",),
        )).fetchone())["id"]
    session = uuid4()
    consent = await provider.consent_preview(request_id, owner, session)
    target = await provider.approve_consent(request_id, owner, session, consent["csrf_token"])
    code = parse_qs(urlsplit(target).query)["code"][0]
    assert parse_qs(urlsplit(target).query)["state"] == ["client-state"]
    return client_id, code, owner, params


def exchange_params(provider, client_id, code, **kwargs):
    return {"grant_type": "authorization_code", "client_id": client_id, "code": code,
            "code_verifier": VERIFIER, "redirect_uri": REDIRECT,
            "resource": provider.config.resource, **kwargs}


async def test_pkce_resource_redirect_and_single_use_code(mcp_database):
    provider, client = await oauth_client(mcp_database)
    async with client:
        cid, code, owner, params = await new_grant(mcp_database, provider, client)
        for change in ({"code_verifier": "wrong"}, {"resource": "https://other.invalid/mcp"},
                       {"redirect_uri": "https://other.invalid/callback"}):
            response = await client.post("/token", data=exchange_params(provider, cid, code, **change))
            assert response.status_code == 400
        response = await client.post("/token", data=exchange_params(provider, cid, code))
        assert response.status_code == 200, response.text
        access = await provider.verify_token(response.json()["access_token"])
        assert access.subject == str(owner)
        assert access.resource == "https://cardsp.cn/mcp"
        assert access.scopes == ["cards:read", "cards:write"]
        assert (await client.post("/token", data=exchange_params(provider, cid, code))).status_code == 400
        params["code_challenge_method"] = "plain"
        assert (await client.get("/authorize", params=params)).status_code in (302, 400)
        params["code_challenge_method"] = "S256"
        params["resource"] = "https://other.invalid/mcp"
        response = await client.get("/authorize", params=params)
        assert "error=" in response.headers.get("location", "") or response.status_code == 400


async def test_refresh_rotation_replay_revokes_new_access_token(mcp_database):
    provider, client = await oauth_client(mcp_database)
    async with client:
        cid, code, _, _ = await new_grant(mcp_database, provider, client)
        first = (await client.post("/token", data=exchange_params(provider, cid, code))).json()
        refresh = {"grant_type": "refresh_token", "client_id": cid,
                   "refresh_token": first["refresh_token"], "resource": provider.config.resource}
        response = await client.post("/token", data=refresh)
        assert response.status_code == 200, response.text
        second = response.json()
        assert second["refresh_token"] != first["refresh_token"]
        assert await provider.verify_token(first["access_token"]) is None
        assert await provider.verify_token(second["access_token"]) is not None
        assert (await client.post("/token", data=refresh)).status_code == 400
        assert await provider.verify_token(second["access_token"]) is None


@pytest.mark.parametrize("column,value", [
    ("issuer", "https://wrong.invalid"), ("resource", "https://wrong.invalid/mcp"),
    ("expires_at", "2000-01-01T00:00:00+00:00"),
])
async def test_wrong_issuer_resource_and_expired_tokens_rejected(mcp_database, column, value):
    from psycopg import sql
    provider, client = await oauth_client(mcp_database)
    async with client:
        cid, code, _, _ = await new_grant(mcp_database, provider, client)
        tokens = (await client.post("/token", data=exchange_params(provider, cid, code))).json()
        async with mcp_database.connection() as conn:
            await conn.execute(sql.SQL("UPDATE chatgpt_oauth_token SET {}=%s WHERE token_hash=%s").format(
                sql.Identifier(column)), (value, hashlib.sha256(tokens["access_token"].encode()).hexdigest()))
        assert await provider.verify_token(tokens["access_token"]) is None


async def test_scope_and_owner_never_come_from_tool_arguments(mcp_database):
    from card_platform.mcp.auth import require_principal
    from card_platform.mcp.schemas import McpFailure
    provider, client = await oauth_client(mcp_database)
    async with client:
        cid, code, owner, _ = await new_grant(mcp_database, provider, client)
        tokens = (await client.post("/token", data=exchange_params(provider, cid, code))).json()
        access = await provider.verify_token(tokens["access_token"])
        assert require_principal(access, {"cards:read"}, provider.config).owner_id == owner
        with pytest.raises(McpFailure) as error:
            require_principal(access, {"transactions:write"}, provider.config)
        assert error.value.code == "FORBIDDEN"
        revoked = await client.post("/revoke", data={"client_id": cid, "token": tokens["refresh_token"]})
        assert revoked.status_code == 200
        assert await provider.verify_token(tokens["access_token"]) is None


async def test_registration_rejects_unapproved_redirect_and_scopes(mcp_database):
    _, client = await oauth_client(mcp_database)
    async with client:
        for payload in (
            {"redirect_uris": ["https://attacker.invalid/callback"]},
            {"redirect_uris": [REDIRECT], "scope": "admin"},
        ):
            response = await client.post("/register", json={
                "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"], **payload,
            })
            assert response.status_code == 400


async def test_discovery_describes_only_supported_public_client_auth(mcp_database):
    provider, client = await oauth_client(mcp_database)
    async with client:
        response = await client.get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200
        metadata = response.json()
        assert metadata["issuer"] == provider.config.issuer
        assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
        assert metadata["code_challenge_methods_supported"] == ["S256"]


async def test_oauth_form_body_has_a_bounded_read_budget(mcp_database):
    provider, client = await oauth_client(mcp_database)
    async with client:
        response = await client.post("/token", data={"resource": provider.config.resource,
            "client_id": "unknown", "padding": "x" * (64 * 1024 + 1)})
        assert response.status_code == 413


async def test_consent_is_bound_to_login_session_and_csrf(mcp_database):
    from card_platform.mcp.schemas import McpFailure
    provider, client = await oauth_client(mcp_database)
    async with client:
        cid, _, owner, params = await new_grant(mcp_database, provider, client)
        response = await client.get("/authorize", params=params)
        rid = parse_qs(urlsplit(response.headers["location"]).query)["request_id"][0]
        session = uuid4()
        preview = await provider.consent_preview(rid, owner, session)
        for wrong_owner, wrong_session, csrf in (
            (uuid4(), session, preview["csrf_token"]),
            (owner, uuid4(), preview["csrf_token"]), (owner, session, "forged"),
        ):
            with pytest.raises(McpFailure):
                await provider.approve_consent(rid, wrong_owner, wrong_session, csrf)


async def test_dcr_omitted_scope_can_request_full_supported_consent_without_grant(mcp_database):
    provider,client=await oauth_client(mcp_database)
    async with client:
        registered=await client.post('/register',json={'redirect_uris':[REDIRECT],'token_endpoint_auth_method':'none',
            'grant_types':['authorization_code','refresh_token'],'response_types':['code']})
        assert registered.status_code==201
        response=await client.get('/authorize',params={'client_id':registered.json()['client_id'],'redirect_uri':REDIRECT,
            'response_type':'code','code_challenge':CHALLENGE,'code_challenge_method':'S256','state':'test-state',
            'resource':provider.config.resource,'scope':'cards:read cards:write transactions:write research:run'})
        assert urlsplit(response.headers['location']).path=='/chatgpt/authorize'
        assert 'request_id' in parse_qs(urlsplit(response.headers['location']).query)
