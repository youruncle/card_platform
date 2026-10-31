"""Standard SDK OAuth endpoints plus consent guarded by platform login."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.handlers.revoke import RevocationRequest
from mcp.server.auth.middleware.client_auth import ClientAuthenticator, AuthenticationError
from mcp.server.auth.routes import build_metadata, create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import RequestBodyLimitMiddleware
from pydantic import AnyHttpUrl, ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Route

from card_platform.auth import AuthenticatedUser, current_user
from card_platform.mcp.auth import SCOPES
from card_platform.mcp.schemas import McpFailure, StrictModel


def create_oauth_app(provider):
    options = ClientRegistrationOptions(
        enabled=True, valid_scopes=sorted(SCOPES), default_scopes=sorted(SCOPES),
    )
    routes = create_auth_routes(
        provider, AnyHttpUrl(provider.config.issuer),
        client_registration_options=options,
        revocation_options=RevocationOptions(enabled=True),
    )
    authorize_handler = AuthorizationHandler(provider)
    token_handler = TokenHandler(provider, ClientAuthenticator(provider))

    async def metadata(request):
        value = build_metadata(AnyHttpUrl(provider.config.issuer), None, options,
                               RevocationOptions(enabled=True)).model_dump(mode="json", exclude_none=True)
        value["token_endpoint_auth_methods_supported"] = ["none"]
        value["revocation_endpoint_auth_methods_supported"] = ["none"]
        return JSONResponse(value)

    async def authorize(request: Request):
        params = request.query_params if request.method == "GET" else await request.form()
        if (params.get("code_challenge_method") != "S256"
                or params.get("resource") != provider.config.resource
                or len(params.getlist("resource")) != 1):
            return JSONResponse({"error": "invalid_request"}, status_code=400,
                                headers={"Cache-Control": "no-store"})
        return await authorize_handler.handle(request)

    async def token(request: Request):
        form = await request.form()
        if form.get("resource") != provider.config.resource or len(form.getlist("resource")) != 1:
            return JSONResponse({"error": "invalid_request"}, status_code=400,
                                headers={"Cache-Control": "no-store"})
        return await token_handler.handle(request)

    async def revoke(request: Request):
        # SDK RevocationRequest currently requires nullable client_secret even
        # for public clients. Normalize that omitted field at our boundary.
        try:
            client = await ClientAuthenticator(provider).authenticate_request(request)
            payload = RevocationRequest.model_validate({"client_secret": None, **dict(await request.form())})
        except (ValidationError, AuthenticationError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        credential = await provider.load_access_token(payload.token)
        if credential is None:
            credential = await provider.load_refresh_token(client, payload.token)
        if credential is not None and credential.client_id == client.client_id:
            await provider.revoke_token(credential)
        return JSONResponse({}, headers={"Cache-Control": "no-store"})

    # Keep SDK registration/revocation/discovery; strengthen resource and S256
    # enforcement at the request boundary before delegating protocol handling.
    routes = [r for r in routes if r.path not in {
        "/authorize", "/token", "/revoke", "/.well-known/oauth-authorization-server",
    }]
    routes.extend([Route("/authorize", authorize, methods=["GET", "POST"]),
                   Route("/token", token, methods=["POST"]),
                   Route("/revoke", revoke, methods=["POST"]),
                   Route("/.well-known/oauth-authorization-server", metadata)])
    return Starlette(routes=routes, middleware=[Middleware(RequestBodyLimitMiddleware, max_body_size=64 * 1024)])


router = APIRouter(prefix="/v1/chatgpt", tags=["chatgpt"])


def get_provider(request: Request):
    return request.app.state.chatgpt_oauth


def failure_response(error: McpFailure):
    return JSONResponse(error.public(), status_code=404 if error.code == "NOT_FOUND" else 403,
                        headers={"Cache-Control": "no-store"})


class ConsentApproval(StrictModel):
    csrf_token: str


@router.get("/authorizations/{request_id}")
async def consent_preview(request_id: UUID, request: Request,
                          user: AuthenticatedUser = Depends(current_user)):
    try:
        return await get_provider(request).consent_preview(request_id, user.id, user.session_id)
    except McpFailure as error:
        return failure_response(error)


@router.post("/authorizations/{request_id}/approve")
async def consent_approve(request_id: UUID, payload: ConsentApproval, request: Request,
                          user: AuthenticatedUser = Depends(current_user)):
    provider = get_provider(request)
    if request.headers.get("origin") != provider.config.web_origin:
        return JSONResponse({"code": "FORBIDDEN"}, status_code=403)
    try:
        target = await provider.approve_consent(request_id, user.id, user.session_id, payload.csrf_token)
        return {"redirect_url": target}
    except McpFailure as error:
        return failure_response(error)


@router.get("/connections")
async def list_connections(request: Request, user: AuthenticatedUser = Depends(current_user)):
    async with get_provider(request).database.connection() as conn:
        return await (await conn.execute(
            """SELECT id,scopes,expires_at,revoked_at,created_at FROM chatgpt_connection
               WHERE owner_id=%s ORDER BY created_at DESC LIMIT 100""", (user.id,)
        )).fetchall()


@router.post("/connections/{connection_id}/revoke")
async def revoke_connection(connection_id: UUID, request: Request,
                            user: AuthenticatedUser = Depends(current_user)):
    provider = get_provider(request)
    if request.headers.get("origin") != provider.config.web_origin:
        return JSONResponse({"code": "FORBIDDEN"}, status_code=403)
    async with provider.database.connection() as conn:
        row = await (await conn.execute(
            """UPDATE chatgpt_connection SET revoked_at=COALESCE(revoked_at,now())
               WHERE id=%s AND owner_id=%s RETURNING id""", (connection_id, user.id)
        )).fetchone()
    if not row:
        return JSONResponse({"code": "NOT_FOUND"}, status_code=404)
    return {"connection_id": connection_id, "revoked": True}
