"""SDK OAuth provider with hash-only PostgreSQL credentials and existing owners."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import secrets
from uuid import UUID

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    RefreshToken, RegistrationError, TokenError, construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from psycopg.types.json import Jsonb

from card_platform.mcp.schemas import McpFailure, McpPrincipal

SCOPES = frozenset({"cards:read", "cards:write", "transactions:write", "research:run"})


@dataclass(frozen=True)
class OAuthConfig:
    issuer: str = "https://cardsp.cn/v1/chatgpt/oauth"
    resource: str = "https://cardsp.cn/mcp"
    web_origin: str = "https://cardsp.cn"
    redirect_uris: tuple[str, ...] = ()


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def require_principal(token: AccessToken | None, scopes: set[str], config: OAuthConfig) -> McpPrincipal:
    if (token is None or token.resource != config.resource or not token.expires_at
            or token.expires_at <= datetime.now(timezone.utc).timestamp()
            or (token.claims or {}).get("iss") != config.issuer):
        raise McpFailure("AUTH_REQUIRED", "授权无效或已过期", "重新连接卡牌管家")
    try:
        principal = McpPrincipal(
            owner_id=UUID(token.subject or ""),
            connection_id=UUID((token.claims or {}).get("connection_id", "")),
            scopes=frozenset(token.scopes),
        )
    except (ValueError, TypeError):
        raise McpFailure("AUTH_REQUIRED", "授权主体无效", "重新连接卡牌管家") from None
    if not principal.scopes <= SCOPES or not scopes <= principal.scopes:
        raise McpFailure("FORBIDDEN", "缺少本操作所需权限", "重新授权所需权限")
    return principal


class CardOAuthProvider:
    def __init__(self, database, config: OAuthConfig):
        self.database = database
        self.config = config

    async def get_client(self, client_id: str):
        async with self.database.connection() as conn:
            row = await (await conn.execute(
                "SELECT metadata FROM chatgpt_oauth_client WHERE client_id=%s", (client_id,)
            )).fetchone()
        return OAuthClientInformationFull.model_validate(row["metadata"]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull):
        redirects = {str(x) for x in client_info.redirect_uris or []}
        if not redirects or not redirects <= set(self.config.redirect_uris):
            raise RegistrationError("invalid_redirect_uri", "回调地址未获允许")
        if (client_info.token_endpoint_auth_method != "none"
                or set(client_info.grant_types) != {"authorization_code", "refresh_token"}
                or client_info.response_types != ["code"]
                or not set((client_info.scope or "cards:read").split()) <= SCOPES):
            raise RegistrationError("invalid_client_metadata", "不支持的客户端权限或认证方式")
        async with self.database.connection() as conn:
            await conn.execute(
                "INSERT INTO chatgpt_oauth_client(client_id,metadata) VALUES (%s,%s)",
                (client_info.client_id, Jsonb(client_info.model_dump(mode="json"))),
            )

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams):
        scopes = set(params.scopes or [])
        if params.resource != self.config.resource:
            raise AuthorizeError("invalid_request", "resource不匹配")
        if not params.state or not scopes or not scopes <= SCOPES:
            raise AuthorizeError("invalid_scope", "缺少state或请求权限无效")
        if str(params.redirect_uri) not in self.config.redirect_uris:
            raise AuthorizeError("invalid_request", "回调地址未获允许")
        async with self.database.connection() as conn:
            row = await (await conn.execute(
                "INSERT INTO chatgpt_oauth_request(client_id,params) VALUES (%s,%s) RETURNING id",
                (client.client_id, Jsonb(params.model_dump(mode="json"))),
            )).fetchone()
        return f"{self.config.web_origin}/chatgpt/authorize?request_id={row['id']}"

    async def consent_preview(self, request_id, owner_id: UUID, session_id: UUID):
        csrf = secrets.token_urlsafe(32)
        async with self.database.connection() as conn:
            row = await (await conn.execute(
                """UPDATE chatgpt_oauth_request SET owner_id=%s,consent_session_id=%s,csrf_hash=%s
                   WHERE id=%s AND approved_at IS NULL AND expires_at>now()
                   AND (owner_id IS NULL OR owner_id=%s) RETURNING params,client_id""",
                (owner_id, session_id, token_hash(csrf), UUID(str(request_id)), owner_id),
            )).fetchone()
        if row is None:
            raise McpFailure("NOT_FOUND", "授权请求不存在或已失效", "从ChatGPT重新连接")
        client = await self.get_client(row["client_id"])
        return {"client_name": client.client_name or "ChatGPT", "scopes": row["params"]["scopes"],
                "resource": self.config.resource, "csrf_token": csrf}

    async def approve_consent(self, request_id, owner_id: UUID, session_id: UUID, csrf: str):
        code = secrets.token_urlsafe(48)
        async with self.database.connection() as conn:
            row = await (await conn.execute(
                """UPDATE chatgpt_oauth_request SET approved_at=now()
                   WHERE id=%s AND owner_id=%s AND consent_session_id=%s AND csrf_hash=%s
                   AND approved_at IS NULL AND expires_at>now() RETURNING params,client_id""",
                (UUID(str(request_id)), owner_id, session_id, token_hash(csrf)),
            )).fetchone()
            if row is None:
                raise McpFailure("FORBIDDEN", "授权摘要、登录或CSRF已失效", "刷新授权页重新核对")
            params = row["params"]
            grant = await (await conn.execute(
                """INSERT INTO chatgpt_connection(owner_id,client_id,scopes)
                   VALUES (%s,%s,%s) RETURNING id""",
                (owner_id, row["client_id"], params["scopes"]),
            )).fetchone()
            await conn.execute(
                "INSERT INTO chatgpt_oauth_code(code_hash,connection_id,params) VALUES (%s,%s,%s)",
                (token_hash(code), grant["id"], Jsonb(params)),
            )
        return construct_redirect_uri(params["redirect_uri"], code=code, state=params["state"])

    async def load_authorization_code(self, client, authorization_code):
        async with self.database.connection() as conn:
            row = await (await conn.execute(
                """SELECT c.*,g.client_id,g.owner_id FROM chatgpt_oauth_code c
                   JOIN chatgpt_connection g ON g.id=c.connection_id
                   JOIN app_user u ON u.id=g.owner_id
                   WHERE c.code_hash=%s AND g.client_id=%s AND c.consumed_at IS NULL
                   AND c.expires_at>now() AND g.revoked_at IS NULL AND u.disabled_at IS NULL""",
                (token_hash(authorization_code), client.client_id),
            )).fetchone()
        if not row:
            return None
        return AuthorizationCode(code=authorization_code, client_id=client.client_id,
                                 subject=str(row["owner_id"]), expires_at=row["expires_at"].timestamp(),
                                 **row["params"])

    async def _issue(self, conn, connection_id, scopes):
        access, refresh = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
        for value, kind, ttl in ((access, "access", 900), (refresh, "refresh", 30 * 86400)):
            await conn.execute(
                """INSERT INTO chatgpt_oauth_token
                   (token_hash,connection_id,kind,issuer,resource,scopes,expires_at)
                   SELECT %s,id,%s,%s,%s,%s,LEAST(now()+make_interval(secs=>%s),expires_at)
                   FROM chatgpt_connection WHERE id=%s""",
                (token_hash(value), kind, self.config.issuer, self.config.resource,
                 scopes, ttl, connection_id),
            )
        return OAuthToken(access_token=access, refresh_token=refresh, token_type="Bearer",
                          expires_in=900, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        async with self.database.connection() as conn:
            row = await (await conn.execute(
                """UPDATE chatgpt_oauth_code c SET consumed_at=now() FROM chatgpt_connection g
                   WHERE c.connection_id=g.id AND c.code_hash=%s AND c.consumed_at IS NULL
                   AND c.expires_at>now() AND g.client_id=%s AND g.revoked_at IS NULL
                   RETURNING c.connection_id""",
                (token_hash(authorization_code.code), client.client_id),
            )).fetchone()
            if row is None:
                raise TokenError("invalid_grant", "授权码已使用或失效")
            return await self._issue(conn, row["connection_id"], authorization_code.scopes)

    async def _load_token(self, token, kind, client_id=None):
        async with self.database.connection() as conn:
            row = await (await conn.execute(
                """SELECT t.*,g.owner_id,g.client_id FROM chatgpt_oauth_token t
                   JOIN chatgpt_connection g ON g.id=t.connection_id
                   JOIN app_user u ON u.id=g.owner_id
                   WHERE t.token_hash=%s AND t.kind=%s AND g.revoked_at IS NULL
                   AND g.expires_at>now() AND t.revoked_at IS NULL AND t.expires_at>now()
                   AND t.issuer=%s AND t.resource=%s AND u.disabled_at IS NULL
                   AND t.scopes <@ g.scopes""",
                (token_hash(token), kind, self.config.issuer, self.config.resource),
            )).fetchone()
        if not row or (client_id is not None and row["client_id"] != client_id):
            return None
        return row

    async def load_refresh_token(self, client, refresh_token):
        row = await self._load_token(refresh_token, "refresh", client.client_id)
        if row is None:
            return None
        # Consumed refresh records must remain discoverable for reuse detection.
        return RefreshToken(token=refresh_token, client_id=client.client_id, scopes=row["scopes"],
                            expires_at=int(row["expires_at"].timestamp()),
                            resource=row["resource"], subject=str(row["owner_id"]))

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        result = None
        async with self.database.connection() as conn:
            row = await (await conn.execute(
                """SELECT t.*,g.revoked_at AS grant_revoked FROM chatgpt_oauth_token t
                   JOIN chatgpt_connection g ON g.id=t.connection_id
                   WHERE t.token_hash=%s AND g.client_id=%s FOR UPDATE OF g,t""",
                (token_hash(refresh_token.token), client.client_id),
            )).fetchone()
            if row and row["consumed_at"] is not None:
                # Commit the revocation BEFORE returning an invalid_grant response.
                await conn.execute("UPDATE chatgpt_connection SET revoked_at=now() WHERE id=%s",
                                   (row["connection_id"],))
            elif (row and row["grant_revoked"] is None and row["expires_at"] > datetime.now(timezone.utc)
                  and set(scopes) <= set(row["scopes"])):
                await conn.execute("UPDATE chatgpt_oauth_token SET consumed_at=now() WHERE token_hash=%s",
                                   (token_hash(refresh_token.token),))
                await conn.execute(
                    "UPDATE chatgpt_oauth_token SET revoked_at=now() WHERE connection_id=%s AND kind='access'",
                    (row["connection_id"],),
                )
                result = await self._issue(conn, row["connection_id"], scopes)
        if result is None:
            raise TokenError("invalid_grant", "刷新凭证已重放或失效，请重新连接")
        return result

    async def load_access_token(self, token):
        row = await self._load_token(token, "access")
        if row is None or row["consumed_at"] is not None:
            return None
        return AccessToken(token=token, client_id=row["client_id"], scopes=row["scopes"],
                           expires_at=int(row["expires_at"].timestamp()), resource=row["resource"],
                           subject=str(row["owner_id"]), claims={"iss": row["issuer"],
                           "connection_id": str(row["connection_id"])})

    async def verify_token(self, token):
        return await self.load_access_token(token)

    async def revoke_token(self, token):
        async with self.database.connection() as conn:
            await conn.execute(
                """UPDATE chatgpt_connection SET revoked_at=now() WHERE id=(
                   SELECT connection_id FROM chatgpt_oauth_token WHERE token_hash=%s)""",
                (token_hash(token.token),),
            )
