from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from psycopg import AsyncConnection

from card_platform.auth import (
    AuthenticatedUser,
    audit,
    create_session,
    current_user,
    normalize_email,
    password_hasher,
    password_matches,
)
from card_platform.config import get_settings
from card_platform.schemas import (
    AuthLogin,
    AuthPasswordChange,
    AuthRegister,
    AuthSessionView,
    AuthUser,
)


router = APIRouter(prefix="/v1/auth", tags=["auth"])


async def connection(request: Request):
    async with request.app.state.database.connection() as value:
        yield value


def set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        expires=expires_at,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/register", response_model=AuthUser, status_code=status.HTTP_201_CREATED)
async def register(
    payload: AuthRegister,
    request: Request,
    response: Response,
    db: AsyncConnection = Depends(connection),
) -> AuthUser:
    settings = get_settings()
    if not settings.auth_registration_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前未开放注册")
    try:
        email = normalize_email(payload.email)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))
    duplicate = False
    async with db.transaction():
        existing = await db.execute(
            """
            SELECT u.id, c.user_id AS credential_user_id
            FROM app_user u
            LEFT JOIN user_credential c ON c.user_id = u.id
            WHERE u.email = %s
            FOR UPDATE OF u
            """,
            (email,),
        )
        row = await existing.fetchone()
        if row and row["credential_user_id"]:
            await audit(db, request, "register", False, email=email, details={"reason": "exists"})
            duplicate = True
        first = await db.execute(
            "SELECT NOT EXISTS (SELECT 1 FROM user_credential) AS is_first"
        )
        is_first = (await first.fetchone())["is_first"]
        if duplicate:
            pass
        elif row:
            user_id = row["id"]
            await db.execute(
                "UPDATE app_user SET display_name = %s, base_currency = %s, role = CASE WHEN %s THEN 'admin' ELSE role END, updated_at = now() WHERE id = %s",
                (payload.display_name, payload.base_currency.upper(), is_first, user_id),
            )
        else:
            role = "admin" if is_first else "member"
            created = await db.execute(
                """
                INSERT INTO app_user (email, display_name, base_currency, role)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (email, payload.display_name, payload.base_currency.upper(), role),
            )
            user_id = (await created.fetchone())["id"]
        if not duplicate:
            await db.execute(
                "INSERT INTO user_credential (user_id, password_hash) VALUES (%s, %s)",
                (user_id, password_hasher.hash(payload.password)),
            )
            token, expires_at = await create_session(db, request, user_id)
            await audit(db, request, "register", True, user_id=user_id, email=email)
            found = await db.execute(
                "SELECT id, email, display_name, base_currency, role FROM app_user WHERE id = %s",
                (user_id,),
            )
            user = AuthUser.model_validate(await found.fetchone())
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该邮箱已注册")
    set_session_cookie(response, token, expires_at)
    return user


@router.post("/login", response_model=AuthUser)
async def login(
    payload: AuthLogin,
    request: Request,
    response: Response,
    db: AsyncConnection = Depends(connection),
) -> AuthUser:
    settings = get_settings()
    try:
        email = normalize_email(payload.email)
    except ValueError:
        email = payload.email.strip().casefold()
    login_failed = False
    async with db.transaction():
        result = await db.execute(
            """
            SELECT u.id, u.email, u.display_name, u.base_currency, u.role,
                   u.disabled_at, c.password_hash, c.failed_login_count, c.locked_until
            FROM app_user u
            JOIN user_credential c ON c.user_id = u.id
            WHERE u.email = %s
            FOR UPDATE OF c
            """,
            (email,),
        )
        row = await result.fetchone()
        now = datetime.now(timezone.utc)
        locked = row and row["locked_until"] and row["locked_until"] > now
        valid = bool(row and not row["disabled_at"] and not locked and password_matches(row["password_hash"], payload.password))
        if not valid:
            if row and not locked:
                failures = row["failed_login_count"] + 1
                await db.execute(
                    """
                    UPDATE user_credential
                    SET failed_login_count = %s,
                        locked_until = CASE WHEN %s >= %s THEN now() + (%s * interval '1 minute') ELSE NULL END,
                        updated_at = now()
                    WHERE user_id = %s
                    """,
                    (failures, failures, settings.auth_max_failed_logins, settings.auth_lock_minutes, row["id"]),
                )
            await audit(db, request, "login", False, user_id=row["id"] if row else None, email=email)
            login_failed = True
        else:
            await db.execute(
                "UPDATE user_credential SET failed_login_count = 0, locked_until = NULL, updated_at = now() WHERE user_id = %s",
                (row["id"],),
            )
            if password_hasher.check_needs_rehash(row["password_hash"]):
                await db.execute(
                    "UPDATE user_credential SET password_hash = %s, password_changed_at = now() WHERE user_id = %s",
                    (password_hasher.hash(payload.password), row["id"]),
                )
            token, expires_at = await create_session(db, request, row["id"])
            await audit(db, request, "login", True, user_id=row["id"], email=email)
            user = AuthUser.model_validate(row)
    if login_failed:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    set_session_cookie(response, token, expires_at)
    return user


@router.get("/me", response_model=AuthUser)
async def me(user: AuthenticatedUser = Depends(current_user)) -> AuthUser:
    return user.public()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> None:
    await db.execute("UPDATE auth_session SET revoked_at = now() WHERE id = %s", (user.session_id,))
    await audit(db, request, "logout", True, user_id=user.id, email=user.email)
    response.delete_cookie(get_settings().auth_cookie_name, path="/")


@router.get("/sessions", response_model=list[AuthSessionView])
async def sessions(
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> list[AuthSessionView]:
    result = await db.execute(
        """
        SELECT id, id = %s AS current, user_agent, host(ip_address) AS ip_address,
               last_seen_at, expires_at, created_at
        FROM auth_session
        WHERE user_id = %s AND revoked_at IS NULL AND expires_at > now()
        ORDER BY last_seen_at DESC
        """,
        (user.session_id, user.id),
    )
    return [AuthSessionView.model_validate(row) for row in await result.fetchall()]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> None:
    await db.execute(
        "UPDATE auth_session SET revoked_at = now() WHERE id = %s AND user_id = %s",
        (session_id, user.id),
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: AuthPasswordChange,
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> None:
    async with db.transaction():
        result = await db.execute(
            "SELECT password_hash FROM user_credential WHERE user_id = %s FOR UPDATE",
            (user.id,),
        )
        credential = await result.fetchone()
        if credential is None or not password_matches(
            credential["password_hash"], payload.current_password
        ):
            await audit(
                db,
                request,
                "change_password",
                False,
                user_id=user.id,
                email=user.email,
            )
            failure = True
        else:
            failure = False
            await db.execute(
                """
                UPDATE user_credential
                SET password_hash = %s, password_changed_at = now(), updated_at = now()
                WHERE user_id = %s
                """,
                (password_hasher.hash(payload.new_password), user.id),
            )
            await db.execute(
                """
                UPDATE auth_session SET revoked_at = now()
                WHERE user_id = %s AND id <> %s AND revoked_at IS NULL
                """,
                (user.id, user.session_id),
            )
            await audit(
                db,
                request,
                "change_password",
                True,
                user_id=user.id,
                email=user.email,
            )
    if failure:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="当前密码不正确")
