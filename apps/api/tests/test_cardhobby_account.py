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


def test_account_session_ignores_unavailable_global_proxy(monkeypatch):
    """A global proxy for overseas providers must not route CardHobby login."""
    from card_platform.cardhobby_account import new_client

    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.setenv(name, 'http://127.0.0.1:1')
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv('NO_PROXY', '')
    monkeypatch.delenv('no_proxy', raising=False)
    seen = []

    async def transport_request(transport, request):
        # Exercise HTTPX's actual environment routing, without external traffic.
        seen.append(type(transport._pool).__name__)
        if 'Proxy' in seen[-1]:
            raise httpx.ConnectError('unavailable proxy', request=request)
        if request.url.path.lower().startswith('/order/'):
            return httpx.Response(302, headers={'location': '/Verify'}, request=request)
        return httpx.Response(200, text=LOGIN, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', transport_request)

    async def scenario():
        async with new_client() as client:
            assert await CardHobbySession(client).check() is False
    asyncio.run(scenario())
    assert seen == ['AsyncConnectionPool', 'AsyncConnectionPool']


@pytest.mark.parametrize('pager', [
    '<a disabled="disabled">»</a>',
    '<span disabled="disabled"><a href="?searchTxt=Test&pageindex=2">»</a></span>',
    '<a aria-disabled="true" href="?searchTxt=Test&pageindex=2">»</a>',
])
def test_disabled_terminal_pager_is_complete_without_repeating_page(pager):
    async def scenario():
        remote = Remote()
        remote.orders = ORDER + pager
        async with remote.client() as client:
            session = CardHobbySession(client)
            await session.login(CREDENTIALS)
            remote.requests.clear()
            result = await session.read_orders(['Test'])
            assert result['complete'] is True
            assert len(result['orders']) == 1
            assert sum(r.url.path.lower() == '/order/myorders' for r in remote.requests) == 1
    asyncio.run(scenario())


def test_live_order_read_preserves_collect_method_and_online_total():
    class CollectRemote(Remote):
        def handle(self, request):
            if request.url.path.lower().startswith('/order/detail/'):
                return httpx.Response(200, text='<li><div>运费：</div><div>顺丰到付</div></li><li>实付总额：￥30.00</li>')
            return super().handle(request)
    async def scenario():
        remote = CollectRemote()
        remote.orders = ORDER.replace('30.10', '30.00')
        async with remote.client() as client:
            session = CardHobbySession(client)
            assert await session.login(CREDENTIALS) is None
            result = await session.read_orders(['Test Player'])
            assert result['complete']
            captured = result['orders'][0]
            assert captured['shipping_method'] == 'sf_collect'
            assert captured['shipping'] is None
            assert captured['paid_total'] == '30.00'
            assert captured['displayed_total'] == '30.00'
    asyncio.run(scenario())


def test_detail_service_fee_with_help_dialog_is_captured_as_extra_fee():
    doc = Document('<li><div>服务费?：</div><div>￥2.00<div>服务费说明 '
                   + '支付规则。' * 30 + '</div></div></li><li>实付总额：￥8.01</li>')
    parsed = detail_amounts(doc)
    assert parsed['extra_fee'] == '2.00'
    assert parsed['paid_total'] == '8.01'
    unrelated = detail_amounts(Document('<div>服务费说明：每笔另收￥2.00</div><li>实付总额：￥6.01</li>'))
    assert unrelated['extra_fee'] is None
