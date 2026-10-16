"""Owner-isolated CardHobby sessions. Credentials never leave the backend."""

import asyncio
import logging
import math
import re
from contextvars import ContextVar
from time import monotonic
from datetime import datetime, timedelta, timezone
from http.cookiejar import Cookie
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

from card_platform.cardhobby_order_html import (
    ORIGIN, Document, detail_amounts, parse_orders, recognized_list, safe_url,
)
from card_platform.purchase_cost import OrderCapture

logger = logging.getLogger(__name__)
request_stage = ContextVar('cardhobby_request_stage', default='prepare')
STAGE_LABELS = {'prepare':'准备查询', 'session_check':'检查登录状态', 'login_page':'打开登录页',
                'login_submit':'提交登录验证', 'sms_send':'发送短信验证码', 'order_search':'搜索购买订单', 'order_detail':'读取订单详情'}


class AccountError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def login_required():
    return AccountError('LOGIN_REQUIRED', '请登录并绑定当前用户的卡淘账号，成功后将自动继续获取成本')


def cookie_data(client):
    return [dict(name=c.name, value=c.value, domain=c.domain, path=c.path, expires=c.expires)
            for c in client.cookies.jar if c.domain.lstrip('.') in {'cardhobby.com.cn', 'www.cardhobby.com.cn'}]


def restore_cookies(client, cookies):
    for c in cookies:
        if c['domain'].lstrip('.') not in {'cardhobby.com.cn', 'www.cardhobby.com.cn'}:
            continue
        client.cookies.jar.set_cookie(Cookie(
            0, c['name'], c['value'], None, False, c['domain'], True,
            c['domain'].startswith('.'), c['path'], True, True, c['expires'],
            c['expires'] is None, None, None, {}, False,
        ))


def new_client():
    # CardHobby is directly reachable. The global proxy used by overseas
    # providers may be offline; it must not block session checks or re-login.
    return httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(connect=8, read=25, write=10, pool=5), follow_redirects=False, headers={
        'User-Agent': 'Mozilla/5.0 CardPlatform/1.0', 'Accept': 'text/html',
    })


class CardHobbySession:
    def __init__(self, client):
        self.client = client

    async def request(self, url, *, data=None, stage='prepare'):
        request_stage.set(stage)
        url = safe_url(url)
        for _ in range(6):
            attempts = 2 if data is None else 1
            for attempt in range(1, attempts + 1):
                started = monotonic()
                try:
                    response = await self.client.request('GET' if data is None else 'POST', url, data=data)
                except httpx.TransportError as error:
                    # Never log request bodies, cookie values, query strings or raw exceptions.
                    logger.warning('cardhobby_request stage=%s method=%s attempt=%s error_type=%s elapsed_ms=%s',
                                   stage, 'GET' if data is None else 'POST', attempt, type(error).__name__, round((monotonic()-started)*1000))
                    if attempt < attempts:
                        await asyncio.sleep(0.5)
                        continue
                    suffix = '已自动重试一次，仍未恢复' if attempts > 1 else '未自动重复提交登录'
                    raise AccountError('NETWORK_ERROR', f"卡淘{STAGE_LABELS[stage]}时网络中断或超时；{suffix}。请稍后重试，原成本未修改") from None
                logger.info('cardhobby_request stage=%s attempt=%s status=%s elapsed_ms=%s',
                            stage, attempt, response.status_code, round((monotonic()-started)*1000))
                if response.status_code in {502, 503, 504} and attempt < attempts:
                    logger.warning('cardhobby_request stage=%s attempt=%s status=%s retry=1', stage, attempt, response.status_code)
                    await asyncio.sleep(0.5)
                    continue
                break
            if response.status_code in {301, 302, 303, 307, 308}:
                url = safe_url(urljoin(url, response.headers.get('location', '')))
                if data is not None and response.status_code in {307, 308}:
                    raise AccountError('UNAVAILABLE', '卡淘登录跳转异常，已停止')
                data = None
                continue
            if response.status_code in {401, 403}:
                raise login_required()
            if response.status_code == 429:
                raise AccountError('RATE_LIMITED', '卡淘请求过于频繁，请稍后再试')
            if response.status_code != 200 or len(response.content) > 8_000_000:
                raise AccountError('UNAVAILABLE', f'卡淘{STAGE_LABELS[stage]}暂不可用，请稍后重试；原成本未修改')
            return response
        raise AccountError('UNAVAILABLE', '卡淘跳转次数异常，已停止')

    async def check(self):
        try:
            response = await self.request('/order/myorders?status=0', stage='session_check')
        except AccountError as error:
            if error.code == 'LOGIN_REQUIRED':
                return False
            raise
        doc = Document(response.text)
        if re.fullmatch(r'/verify(?:/index)?/?', response.url.path, re.I):
            return False
        if not recognized_list(doc):
            raise AccountError('UNAVAILABLE', '卡淘订单页面结构未识别，未将其当作登录失效或空订单')
        return True

    async def login(self, credentials, pending=None, sms_code=None):
        if pending:
            token = pending['token']
        else:
            response = await self.request('/Verify', stage='login_page')
            doc = Document(response.text)
            form = doc.by_id('loginForm')
            if form is None or safe_url(form.attrs.get('action', ''), 'login') != ORIGIN+'/Verify/Index':
                raise AccountError('UNAVAILABLE', '卡淘登录页面结构变化，已停止提交密码')
            token = next((n.attrs.get('value') for n in form.all('input')
                          if n.attrs.get('name') == '__RequestVerificationToken'), None)
            if not token:
                raise AccountError('UNAVAILABLE', '卡淘登录校验令牌缺失，已停止提交密码')
        fields = dict(username=credentials['username'], password=credentials['password'],
                      isAgree='on', __RequestVerificationToken=token)
        if sms_code:
            fields['smsCode'] = sms_code
        response = await self.request('/Verify/Index', data=fields, stage='login_submit')
        if re.search(r'\b(?:var|let|const)\s+needVerify\s*=\s*[\x22\x27]1[\x22\x27]', response.text):
            doc = Document(response.text)
            token = next((n.attrs.get('value') for n in doc.all('input')
                          if n.attrs.get('name') == '__RequestVerificationToken'), token)
            return {'token': token, 'expires': (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}
        if not await self.check():
            raise AccountError('LOGIN_REJECTED', '卡淘登录未成功，请核对账号密码；如有风控限制，请稍后重试')
        return None

    async def read_orders(self, keywords, max_pages=10, max_orders=100):
        orders, seen = {}, set()
        pages = 0
        for keyword in dict.fromkeys(keywords):
            url = ORIGIN+'/order/myorders?'+urlencode(dict(
                status=0, code='', idcardName='', searchTxt=keyword, Order_Tag='',
                beginDate='2000-01-01 00:00:00', endDate=f'{datetime.now().year}-12-31 23:59:59',
            ))
            while url:
                if pages >= max_pages or len(orders) >= max_orders:
                    return {'orders': list(orders.values()), 'complete': False}
                safe_url(url, 'list')
                if url in seen:
                    raise ValueError('卡淘订单分页重复，未填写成本')
                seen.add(url)
                response = await self.request(url, stage='order_search')
                if re.match(r'/verify(?:/|$)', response.url.path, re.I):
                    raise login_required()
                safe_url(str(response.url), 'list')
                doc = Document(response.text)
                if not recognized_list(doc):
                    raise ValueError('卡淘订单结构未识别，未填写成本')
                pages += 1
                for order in parse_orders(doc):
                    if order.external_order_id in orders:
                        continue
                    if len(orders) >= max_orders:
                        return {'orders': list(orders.values()), 'complete': False}
                    detail = await self.request(safe_url(order.source_url, 'detail'), stage='order_detail')
                    if re.match(r'/verify(?:/|$)', detail.url.path, re.I):
                        raise login_required()
                    safe_url(str(detail.url), 'detail')
                    order = OrderCapture.model_validate({
                        **order.model_dump(), **detail_amounts(Document(detail.text)),
                    })
                    if order.paid_total is None:
                        order.parser_errors.append('详情实付金额未核实')
                    orders[order.external_order_id] = order.model_dump(mode='json')
                    await asyncio.sleep(0.15)
                next_link = next((a for a in doc.all('a') if a.text() in {'»', '下一页', '›'}
                                  and not any('disabled' in n.attrs
                                              or n.attrs.get('aria-disabled') == 'true'
                                              or 'disabled' in n.attrs.get('class', '').split()
                                              for n in [a, a.parent] if n)), None)
                url = safe_url(urljoin(url, next_link.attrs.get('href', '')), 'list') if next_link else None
                if url and parse_qs(urlsplit(url).query).get('searchTxt') != [keyword]:
                    raise ValueError('卡淘分页丢失查询词，未继续读取')
                await asyncio.sleep(0.15)
        return {'orders': list(orders.values()), 'complete': True}


class CardHobbyAccountService:
    def __init__(self, cipher, client_factory=new_client):
        self.cipher = cipher
        self.client_factory = client_factory

    async def row(self, conn, owner):
        # Serialize credential changes, login and reads for this owner across processes.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ('cardhobby:'+str(owner),))
        await conn.execute('INSERT INTO cardhobby_account(owner_id) VALUES(%s) ON CONFLICT DO NOTHING', (owner,))
        result = await conn.execute('SELECT * FROM cardhobby_account WHERE owner_id=%s FOR UPDATE', (owner,))
        return await result.fetchone()

    async def status(self, conn, owner):
        result = await conn.execute('SELECT credentials_enc, state FROM cardhobby_account WHERE owner_id=%s', (owner,))
        row = await result.fetchone()
        return {'saved': bool(row and row['credentials_enc']), 'state': row['state'] if row else 'unbound'}

    async def pending_verification(self, conn, owner):
        row = await self.row(conn, owner)
        if not row['pending_enc']:
            raise login_required()
        pending = self.cipher.decrypt(owner, 'pending', row['pending_enc'])
        if datetime.fromisoformat(pending['expires']) <= datetime.now(timezone.utc):
            raise AccountError('VERIFICATION_EXPIRED', '验证码会话已过期，请重新发起验证；无需重复输入账号密码')
        if not re.fullmatch(r'[0-9]{11}', pending['credentials']['username']):
            raise AccountError('LOGIN_REQUIRED', '卡淘短信验证需要手机号，请使用绑定的11位手机号重新登录')
        return pending

    @staticmethod
    def verification_details(pending):
        retry_at = pending.get('sms_retry_after')
        seconds = max(0, math.ceil((datetime.fromisoformat(retry_at) - datetime.now(timezone.utc)).total_seconds())) if retry_at else 0
        return {'captcha_url': 'https://cms.cardhobby.com.cn/checkcode/index?' + urlencode({'mobile': pending['credentials']['username']}),
                'sms_sent': pending.get('sms_sent') is True, 'retry_after_seconds': seconds}

    async def verification(self, conn, owner):
        return self.verification_details(await self.pending_verification(conn, owner))

    async def save_pending(self, conn, owner, pending):
        await conn.execute('UPDATE cardhobby_account SET pending_enc=%s WHERE owner_id=%s',
                           (self.cipher.encrypt(owner, 'pending', pending), owner))

    async def send_sms(self, conn, owner):
        # Called only after the user completes the official iframe. CardHobby
        # validates the mobile's captcha server-side; never synthesize a solution.
        pending = await self.pending_verification(conn, owner)
        if self.verification_details(pending)['retry_after_seconds']:
            raise AccountError('RATE_LIMITED', '短信请求过于频繁，请稍后重新完成滑块验证')
        pending['sms_retry_after'] = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        pending['sms_sent'] = False
        await self.save_pending(conn, owner, pending)
        async with self.client_factory() as client:
            restore_cookies(client, pending['cookies'])
            client.headers.update({'X-Requested-With': 'XMLHttpRequest',
                                   'Referer': ORIGIN + '/Verify/Index', 'Accept': 'application/json'})
            response = await CardHobbySession(client).request('/Verify/SendSMS/', data={
                'mobile': '86 ' + pending['credentials']['username'], 'SecurityCode': '', 'smsType': 9,
            }, stage='sms_send')
            try:
                result = response.json()
            except ValueError:
                result = None
            if not isinstance(result, dict) or str(result.get('status')) != '1':
                raise AccountError('SMS_SEND_FAILED', '卡淘未确认短信发送成功，请稍后重新完成滑块验证并重试')
            pending['sms_sent'] = True
            pending['cookies'] = cookie_data(client)
            await self.save_pending(conn, owner, pending)
        return self.verification_details(pending)

    async def save_session(self, conn, owner, client, credentials=None):
        token = self.cipher.encrypt(owner, 'session', cookie_data(client))
        if credentials is not None:
            await conn.execute('UPDATE cardhobby_account SET credentials_enc=%s WHERE owner_id=%s',
                               (self.cipher.encrypt(owner, 'credentials', credentials), owner))
        await conn.execute("UPDATE cardhobby_account SET session_enc=%s, pending_enc=NULL, state='connected', retry_after=NULL, updated_at=now() WHERE owner_id=%s", (token, owner))

    async def login(self, conn, owner, credentials=None, sms_code=None):
        row = await self.row(conn, owner)
        self.check_cooldown(row)
        pending = None
        if credentials is None and not sms_code:
            # A fresh login uses retained credentials, never expired cookies/tokens.
            if row['pending_enc']:
                credentials = self.cipher.decrypt(owner, 'pending', row['pending_enc'])['credentials']
            elif row['credentials_enc']:
                credentials = self.cipher.decrypt(owner, 'credentials', row['credentials_enc'])
            else:
                raise login_required()
        if sms_code:
            if not row['pending_enc']:
                raise login_required()
            pending = self.cipher.decrypt(owner, 'pending', row['pending_enc'])
            if datetime.fromisoformat(pending['expires']) <= datetime.now(timezone.utc):
                raise AccountError('VERIFICATION_EXPIRED', '验证码会话已过期，请重新发起验证；无需重复输入账号密码')
            credentials = pending['credentials']
        async with self.client_factory() as client:
            if pending:
                restore_cookies(client, pending['cookies'])
            await self.perform_login(conn, owner, client, credentials, pending, sms_code)
        return {'saved': True, 'state': 'connected'}

    @staticmethod
    def check_cooldown(row):
        if row['retry_after'] and row['retry_after'] > datetime.now(timezone.utc):
            raise AccountError('RATE_LIMITED', '为避免卡淘账号锁定，请一分钟后再试')

    async def perform_login(self, conn, owner, client, credentials, pending=None, sms_code=None):
        # The API commits this cooldown on network/auth errors; searches are owner-serialized.
        await conn.execute("UPDATE cardhobby_account SET retry_after=now()+interval '60 seconds' WHERE owner_id=%s", (owner,))
        try:
            challenge = await CardHobbySession(client).login(credentials, pending, sms_code)
        except AccountError as error:
            if error.code in {'LOGIN_REJECTED', 'LOGIN_REQUIRED'}:
                await conn.execute("UPDATE cardhobby_account SET state='login_required', session_enc=NULL, pending_enc=NULL WHERE owner_id=%s", (owner,))
            raise
        if challenge:
            # Transient encrypted challenge state only; permanent credentials are saved after verification.
            attempts = (pending or {}).get('attempts', 0) + bool(sms_code)
            if attempts >= 3:
                await conn.execute("UPDATE cardhobby_account SET pending_enc=NULL, state='login_required' WHERE owner_id=%s", (owner,))
                raise AccountError('LOGIN_REJECTED', '短信验证多次未成功，请稍后重新登录')
            if pending:
                challenge['expires'] = pending['expires']
                for key in ('sms_sent', 'sms_retry_after'):
                    if key in pending:
                        challenge[key] = pending[key]
            challenge.update(credentials=credentials, cookies=cookie_data(client), attempts=attempts)
            token = self.cipher.encrypt(owner, 'pending', challenge)
            await conn.execute("UPDATE cardhobby_account SET pending_enc=%s, state='verification_required', retry_after=CASE WHEN %s THEN retry_after ELSE NULL END WHERE owner_id=%s", (token, bool(sms_code), owner))
            raise AccountError('VERIFICATION_REQUIRED', '卡淘要求验证，请先完成滑块并获取短信验证码；验证成功后才保存账号')
        await self.save_session(conn, owner, client, credentials)

    async def search(self, conn, owner, keywords):
        row = await self.row(conn, owner)
        if row['state'] == 'verification_required':
            raise AccountError('VERIFICATION_REQUIRED', '卡淘要求短信验证，请先完成验证')
        if not row['credentials_enc'] or row['state'] == 'login_required':
            raise login_required()
        self.check_cooldown(row)
        credentials = self.cipher.decrypt(owner, 'credentials', row['credentials_enc'])
        async with self.client_factory() as client:
            if row['session_enc']:
                restore_cookies(client, self.cipher.decrypt(owner, 'session', row['session_enc']))
            session = CardHobbySession(client)
            logged_in = await session.check()
            if not logged_in:
                await self.perform_login(conn, owner, client, credentials)
            try:
                result = await session.read_orders(keywords)
            except AccountError as error:
                if error.code != 'LOGIN_REQUIRED' or not logged_in:
                    raise
                # One recovery attempt if the session expires during pagination, then restart all reads.
                await self.perform_login(conn, owner, client, credentials)
                result = await session.read_orders(keywords)
            await self.save_session(conn, owner, client)
            return result
