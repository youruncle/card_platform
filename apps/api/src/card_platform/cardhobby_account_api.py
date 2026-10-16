import asyncio
import logging
from time import monotonic
from functools import lru_cache

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field

from card_platform.api import get_connection
from card_platform.auth import AuthenticatedUser, current_user
from card_platform.cardhobby_account import AccountError, CardHobbyAccountService, request_stage, STAGE_LABELS
from card_platform.cardhobby_account_crypto import AccountCipher
from card_platform.config import get_settings

router = APIRouter(prefix='/v1/purchase-cost/cardhobby', dependencies=[Depends(current_user)])
logger = logging.getLogger(__name__)


@lru_cache
def account_service():
    return CardHobbyAccountService(AccountCipher(get_settings().cardhobby_account_key_file))


async def run(conn, owner, operation):
    token = request_stage.set('prepare')
    started = monotonic()
    try:
        async with asyncio.timeout(150):
            return JSONResponse(await operation(), headers={'Cache-Control':'no-store'})
    except AccountError as error:
        # Keep failure/cooldown/challenge state, never return remote HTML or exception bodies.
        await conn.commit()
        logger.warning('cardhobby_account owner=%s stage=%s code=%s elapsed_ms=%s', owner, request_stage.get(), error.code, round((monotonic()-started)*1000))
        return JSONResponse(status_code=503 if error.code == 'NETWORK_ERROR' else 409, content={'detail': {'code': error.code, 'message': str(error)}}, headers={'Cache-Control':'no-store'})
    except (httpx.HTTPError, TimeoutError) as error:
        await conn.commit()
        stage = request_stage.get()
        logger.warning('cardhobby_account owner=%s stage=%s error_type=%s elapsed_ms=%s', owner, stage, type(error).__name__, round((monotonic()-started)*1000))
        return JSONResponse(status_code=503, content={'detail': {'code':'UNAVAILABLE', 'message':f'卡淘{STAGE_LABELS[stage]}超过本次等待时间或网络异常，原成本未修改，请稍后重新获取'}}, headers={'Cache-Control':'no-store'})
    except (ValueError, OSError):
        await conn.commit()
        logger.warning('cardhobby_account owner=%s stage=%s code=INVALID_EVIDENCE_OR_CONFIG', owner, request_stage.get())
        return JSONResponse(status_code=503, content={'detail': {'code':'UNAVAILABLE', 'message':'卡淘页面解析或凭据配置异常，本次未填写成本，请联系管理员检查'}}, headers={'Cache-Control':'no-store'})
    finally:
        request_stage.reset(token)


@router.get('/status')
async def status(user: AuthenticatedUser = Depends(current_user), conn: AsyncConnection = Depends(get_connection)):
    result = await account_service().status(conn, user.id)
    return JSONResponse(result, headers={'Cache-Control':'no-store'})


@router.post('/login')
async def login(request: Request, user: AuthenticatedUser = Depends(current_user), conn: AsyncConnection = Depends(get_connection)):
    # Parse privately: FastAPI's default validation errors can echo submitted password values.
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {'username', 'password'}:
            raise ValueError
        username, password = body['username'], body['password']
        if not isinstance(username, str) or not 1 <= len(username.strip()) <= 120:
            raise ValueError
        if not isinstance(password, str) or not 1 <= len(password) <= 512:
            raise ValueError
    except (ValueError, TypeError):
        return JSONResponse(status_code=422, content={'detail':'请输入有效的卡淘账号和密码'})
    return await run(conn, user.id, lambda: account_service().login(conn, user.id, dict(username=username.strip(), password=password)))


@router.post('/verify')
async def verify(request: Request, user: AuthenticatedUser = Depends(current_user), conn: AsyncConnection = Depends(get_connection)):
    try:
        body = await request.json()
        code = body['code']
        if set(body) != {'code'} or not isinstance(code, str) or not 4 <= len(code) <= 6 or not code.isascii() or not code.isdigit():
            raise ValueError
    except (ValueError, TypeError, KeyError):
        return JSONResponse(status_code=422, content={'detail':'请输入4至6位数字短信验证码'})
    return await run(conn, user.id, lambda: account_service().login(conn, user.id, sms_code=code))


@router.get('/verification')
async def verification(user: AuthenticatedUser = Depends(current_user), conn: AsyncConnection = Depends(get_connection)):
    return await run(conn, user.id, lambda: account_service().verification(conn, user.id))


@router.post('/restart-verification')
async def restart_verification(user: AuthenticatedUser = Depends(current_user), conn: AsyncConnection = Depends(get_connection)):
    return await run(conn, user.id, lambda: account_service().login(conn, user.id))


@router.post('/send-sms')
async def send_sms(user: AuthenticatedUser = Depends(current_user), conn: AsyncConnection = Depends(get_connection)):
    return await run(conn, user.id, lambda: account_service().send_sms(conn, user.id))


@router.delete('/account', status_code=204)
async def forget(user: AuthenticatedUser = Depends(current_user), conn: AsyncConnection = Depends(get_connection)):
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ('cardhobby:'+str(user.id),))
    await conn.execute('DELETE FROM cardhobby_account WHERE owner_id=%s', (user.id,))


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    keywords: list[str] = Field(min_length=1, max_length=3)


@router.post('/search')
async def search(payload: SearchRequest, user: AuthenticatedUser = Depends(current_user), conn: AsyncConnection = Depends(get_connection)):
    if any(not word.strip() or len(word) > 120 for word in payload.keywords):
        return JSONResponse(status_code=422, content={'detail':'搜索关键字无效'})
    return await run(conn, user.id, lambda: account_service().search(conn, user.id, [word.strip() for word in payload.keywords]))
