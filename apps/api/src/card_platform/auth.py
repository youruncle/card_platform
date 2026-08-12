from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.config import get_settings
from card_platform.schemas import AuthUser


password_hasher = PasswordHasher()


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    email: str
    display_name: str
    base_currency: str
    role: str
    session_id: UUID

    def public(self) -> AuthUser:
        return AuthUser(
            id=self.id,
            email=self.email,
            display_name=self.display_name,
            base_currency=self.base_currency,
            role=self.role,
        )


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    local, separator, domain = email.partition("@")
    if not separator or not local or "." not in domain:
        raise ValueError("请输入有效的邮箱地址")
    return email


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def client_metadata(request: Request) -> tuple[str | None, str | None]:
    address = request.client.host if request.client else None
    return address, request.headers.get("user-agent")


async def audit(
    connection: AsyncConnection,
    request: Request,
    event_type: str,
    success: bool,
    *,
    user_id: UUID | None = None,
    email: str | None = None,
    details: dict | None = None,
) -> None:
    address, user_agent = client_metadata(request)
    await connection.execute(
        """
        INSERT INTO auth_audit_log (
            user_id, email, event_type, success, ip_address, user_agent, details
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (user_id, email, event_type, success, address, user_agent, Jsonb(details or {})),
    )


async def create_session(
    connection: AsyncConnection, request: Request, user_id: UUID
) -> tuple[str, datetime]:
    settings = get_settings()
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.auth_session_days)
    address, user_agent = client_metadata(request)
    await connection.execute(
        """
        INSERT INTO auth_session (
            user_id, token_hash, expires_at, ip_address, user_agent
        ) VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, hash_session_token(token), expires_at, address, user_agent),
    )
    return token, expires_at


def password_matches(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


async def current_user(request: Request) -> AuthenticatedUser:
    settings = get_settings()
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    async with request.app.state.database.connection() as connection:
        result = await connection.execute(
            """
            SELECT
                u.id, u.email, u.display_name, u.base_currency, u.role,
                s.id AS session_id
            FROM auth_session s
            JOIN app_user u ON u.id = s.user_id
            WHERE s.token_hash = %s
              AND s.revoked_at IS NULL
              AND s.expires_at > now()
              AND u.disabled_at IS NULL
            """,
            (hash_session_token(token),),
        )
        row = await result.fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期")
        await connection.execute(
            "UPDATE auth_session SET last_seen_at = now() WHERE id = %s",
            (row["session_id"],),
        )
    return AuthenticatedUser(**row)


async def require_admin(
    user: AuthenticatedUser = Depends(current_user),
) -> AuthenticatedUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user
