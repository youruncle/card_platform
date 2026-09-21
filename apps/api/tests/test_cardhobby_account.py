"""Invented HTML/credentials only; no real account or marketplace writes."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from card_platform.cardhobby_account import AccountError, CardHobbySession, cookie_data, restore_cookies
from card_platform.cardhobby_account_crypto import AccountCipher
from card_platform.cardhobby_order_html import Document, detail_amounts, parse_orders, safe_url

LOGIN = '<form id="loginForm" action="/Verify/Index"><input name="__RequestVerificationToken" value="fixture-token"></form>'
LIST = '<input placeholder="商品编号/商品名称">'
ORDER = '''<table><tr><td>2025-09-01 订单号：900001</td><td><a href="/market/seller/9">Fixture Shop</a></td></tr>
<tr><td><a href="/market/item/123"><img src="https://images.cardhobby.com/a.jpg?resize=100"></a><a href="/market/item/123">2025 Topps Test Player</a><span>商品编号: 900002</span></td><td>￥10.00 x2</td><td rowspan="2">总额 ￥30.10 含运费：￥0.10</td><td rowspan="2">已完成<a href="/Order/detail/900001?private=yes">订单详情</a></td></tr>
<tr><td><a href="/market/item/124">Test Player Second Card</a><span>商品编号: 900003</span></td><td>￥10.00 x1</td></tr></table>'''
CREDENTIALS = {'username':'fixture-user', 'password':'fictional-password'}


class Remote:
    def __init__(self):
        self.posts = []
        self.requests = []
        self.session = 'first'
        self.challenge = False
        self.orders = ''
        self.broken = False

    def handle(self, request):
        self.requests.append(request)
        path = request.url.path.lower()
        if path == '/verify/index' and request.method == 'POST':
            fields = parse_qs(request.content.decode())
            self.posts.append(fields)
            assert fields['__RequestVerificationToken'] == ['fixture-token']
            if fields.get('password') != [CREDENTIALS['password']]:
                return httpx.Response(200, text=LOGIN)
            if self.challenge and fields.get('smsCode') != ['123456']:
                return httpx.Response(200, text=LOGIN+'<script>var needVerify = "1";</script>')
            return httpx.Response(302, headers={'location':'/order/myorders', 'set-cookie':f'auth={self.session}; Path=/; Secure; HttpOnly'})
        if path == '/verify':
            return httpx.Response(200, text=LOGIN)
        if f'auth={self.session}' not in request.headers.get('cookie', ''):
            return httpx.Response(302, headers={'location':'/Verify'})
        if path == '/order/myorders':
            return httpx.Response(200, text='unexpected layout' if self.broken else LIST+self.orders)
        if path.startswith('/order/detail/'):
            return httpx.Response(200, text='<p>PRIVATE ADDRESS</p><li>实付总额：￥30.10</li>')
        raise AssertionError(path)

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


def test_cipher_permissions_persistence_owner_and_purpose(tmp_path):
    path = tmp_path/'private'/'key'
    cipher = AccountCipher(str(path))
    token = cipher.encrypt('owner1', 'credentials', CREDENTIALS)
    assert CREDENTIALS['password'] not in token
    assert path.stat().st_mode & 0o777 == 0o600
    assert AccountCipher(str(path)).decrypt('owner1', 'credentials', token) == CREDENTIALS
    for owner, purpose in [('owner2','credentials'), ('owner1','session')]:
        with pytest.raises(ValueError, match='无法解密'):
            cipher.decrypt(owner, purpose, token)
    path.chmod(0o644)
    with pytest.raises(ValueError, match='600'):
        AccountCipher(str(path))


def test_concurrent_key_initialization_publishes_one_complete_key(tmp_path):
    path = str(tmp_path/'key')
    with ThreadPoolExecutor(max_workers=8) as pool:
        ciphers = list(pool.map(lambda _: AccountCipher(path), range(16)))
    token = ciphers[0].encrypt('owner', 'credentials', CREDENTIALS)
    assert all(cipher.decrypt('owner', 'credentials', token) == CREDENTIALS for cipher in ciphers)


@pytest.mark.parametrize('url', ['https://evil.test/Verify', '//evil.test/order/myorders', 'http://cardhobby.com.cn/Verify', 'https://user:pass@cardhobby.com.cn/Verify'])
def test_foreign_or_insecure_urls_rejected(url):
    with pytest.raises(ValueError):
        safe_url(url)


def test_order_parser_public_id_amounts_and_privacy():
    order = parse_orders(Document(LIST+ORDER))[0]
    assert order.external_order_id == '900001'
    assert order.lines[0].item_id == '900002'  # Public ID differs from internal URL id.
    assert order.lines[0].quantity == 2
    assert order.shipping == Decimal('.10')
    assert order.displayed_total == Decimal('30.10')
    assert order.source_url.endswith('/Order/detail/900001')
    assert order.lines[0].image_urls == ['https://images.cardhobby.com/a.jpg']
    assert order.complete
    invalid = parse_orders(Document(ORDER.replace('￥10.00 x1', 'unknown')))[0]
    assert not invalid.complete and invalid.parser_errors
    details = detail_amounts(Document('<p>PRIVATE ADDRESS</p><li>实付总额：￥30.10</li><li>优惠金额：￥2.00</li>'))
    assert details['paid_total'] == '30.10' and details['discount'] == '2.00'
    assert 'PRIVATE' not in str(details)


def test_login_cookies_order_search_and_recovery():
    async def scenario():
        remote = Remote()
        remote.orders = ORDER
        async with remote.client() as client:
            session = CardHobbySession(client)
            assert not await session.check()
            assert await session.login(CREDENTIALS) is None
            assert await session.check()
            cookies = cookie_data(client)
            result = await session.read_orders(['Test Player'])
            assert result['complete'] and len(result['orders']) == 1
            assert result['orders'][0]['paid_total'] == '30.10'
            assert 'PRIVATE' not in str(result)
        async with remote.client() as client:
            restore_cookies(client, cookies)
            assert await CardHobbySession(client).check()
            remote.session = 'expired'
            assert not await CardHobbySession(client).check()
        assert len(remote.posts) == 1
    asyncio.run(scenario())


def test_wrong_password_and_challenge_require_verified_session():
    async def scenario():
        remote = Remote()
        async with remote.client() as client:
            session = CardHobbySession(client)
            with pytest.raises(AccountError, match='未成功'):
                await session.login({**CREDENTIALS, 'password':'wrong'})
            remote.challenge = True
            pending = await session.login(CREDENTIALS)
            assert pending['token'] == 'fixture-token'
            assert not await session.check()
            assert await session.login(CREDENTIALS, pending, '123456') is None
    asyncio.run(scenario())


def test_unknown_page_is_not_empty_orders_or_login_failure():
    async def scenario():
        remote = Remote()
        async with remote.client() as client:
            session = CardHobbySession(client)
            await session.login(CREDENTIALS)
            remote.broken = True
            with pytest.raises(AccountError) as error:
                await session.check()
            assert error.value.code == 'UNAVAILABLE'
            with pytest.raises(ValueError, match='结构未识别'):
                await session.read_orders(['Test'])
    asyncio.run(scenario())


def test_redirect_never_forwards_credentials_to_foreign_origin():
    async def scenario():
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(302, headers={'location':'https://evil.test/Verify'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(ValueError):
                await CardHobbySession(client).request('/Verify/Index', data=CREDENTIALS)
        assert len(requests) == 1 and requests[0].url.host == 'cardhobby.com.cn'
    asyncio.run(scenario())


def test_pagination_limits_and_query_loss_fail_closed():
    async def scenario():
        remote = Remote()
        remote.orders = ORDER+'<a href="/order/myorders?page=2">»</a>'
        async with remote.client() as client:
            session = CardHobbySession(client)
            await session.login(CREDENTIALS)
            with pytest.raises(ValueError, match='丢失查询词'):
                await session.read_orders(['Test'])
            remote.orders = ORDER
            result = await session.read_orders(['Test', 'Second'], max_pages=1)
            assert result['complete'] is False
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['timeout', 'disconnect', 'gateway'])
def test_read_retry_recovers_once_without_duplicate_orders(failure):
    async def scenario():
        remote = Remote()
        remote.orders = ORDER
        details = 0
        def handle(request):
            nonlocal details
            if '/detail/' in request.url.path.lower():
                details += 1
                if details == 1:
                    if failure == 'timeout':
                        raise httpx.ReadTimeout('fixture', request=request)
                    if failure == 'disconnect':
                        raise httpx.RemoteProtocolError('fixture', request=request)
                    return httpx.Response(503)
            return remote.handle(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            session = CardHobbySession(client)
            await session.login(CREDENTIALS)
            result = await session.read_orders(['Test'])
            assert result['complete'] and len(result['orders']) == 1
            assert details == 2 and len(remote.posts) == 1
    asyncio.run(scenario())


def test_exhausted_read_retry_has_safe_stage_specific_error(caplog):
    async def scenario():
        requests = []
        def handle(request):
            requests.append(request)
            raise httpx.ConnectError('sensitive-password-cookie-fixture', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(AccountError) as error:
                await CardHobbySession(client).request('/order/detail/123?private=secret', stage='order_detail')
            assert error.value.code == 'NETWORK_ERROR'
            assert '读取订单详情' in str(error.value) and '重试一次' in str(error.value)
        assert len(requests) == 2
    asyncio.run(scenario())
    assert 'order_detail' in caplog.text and 'ConnectError' in caplog.text
    assert 'sensitive-password' not in caplog.text and 'private=secret' not in caplog.text


def test_login_post_is_not_replayed_on_uncertain_network_failure():
    async def scenario():
        requests = []
        def handle(request):
            requests.append(request)
            raise httpx.ReadTimeout('fixture', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(AccountError) as error:
                await CardHobbySession(client).request('/Verify/Index', data=CREDENTIALS, stage='login_submit')
            assert '未自动重复提交登录' in str(error.value)
        assert len(requests) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('status', [403, 429])
def test_auth_and_rate_limit_responses_are_never_retried(status):
    async def scenario():
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(status)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(AccountError) as error:
                await CardHobbySession(client).request('/order/myorders', stage='order_search')
            assert error.value.code == ('LOGIN_REQUIRED' if status == 403 else 'RATE_LIMITED')
        assert len(requests) == 1
    asyncio.run(scenario())


def test_cancellation_during_retry_is_not_swallowed(monkeypatch):
    async def scenario():
        requests = []
        def handle(request):
            requests.append(request)
            raise httpx.ReadTimeout('fixture', request=request)
        async def cancelled(_):
            raise asyncio.CancelledError
        monkeypatch.setattr('card_platform.cardhobby_account.asyncio.sleep', cancelled)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(asyncio.CancelledError):
                await CardHobbySession(client).request('/order/myorders')
        assert len(requests) == 1
    asyncio.run(scenario())
