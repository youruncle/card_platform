import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from card_platform.cardhobby_account import AccountError, CardHobbyAccountService
from card_platform.cardhobby_account_crypto import AccountCipher


class Connection:
    def __init__(self, row):
        self.value = row

    async def execute(self, sql, args):
        if 'SET pending_enc=%s' in sql:
            self.value['pending_enc'] = args[0]


@pytest.fixture
def context(tmp_path):
    cipher = AccountCipher(str(tmp_path / 'key'))
    pending = {'credentials': {'username': '13800000000', 'password': 'invented'},
               'token': 'fixture', 'cookies': [], 'attempts': 0,
               'expires': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
    conn = Connection({'pending_enc': cipher.encrypt('owner', 'pending', pending)})
    calls = []

    def remote(request):
        calls.append(request)
        return httpx.Response(200, json={'status': '1'})

    service = CardHobbyAccountService(cipher, lambda: httpx.AsyncClient(transport=httpx.MockTransport(remote)))

    async def row(conn, owner):
        assert owner == 'owner'
        return conn.value

    service.row = row
    return service, conn, calls


def test_challenge_does_not_send_sms_and_only_exposes_verification_details(context):
    service, conn, calls = context
    result = asyncio.run(service.verification(conn, 'owner'))
    assert result == {'captcha_url': 'https://cms.cardhobby.com.cn/checkcode/index?mobile=13800000000',
                      'sms_sent': False, 'retry_after_seconds': 0}
    assert not calls
    assert 'invented' not in str(result) and 'fixture' not in str(result)


def test_send_requests_official_sms_endpoint_and_persists_success_and_cooldown(context):
    service, conn, calls = context
    result = asyncio.run(service.send_sms(conn, 'owner'))
    assert result['sms_sent'] is True
    assert 0 < result['retry_after_seconds'] <= 60
    assert len(calls) == 1
    assert str(calls[0].url) == 'https://cardhobby.com.cn/Verify/SendSMS/'
    assert calls[0].headers['X-Requested-With'] == 'XMLHttpRequest'
    assert calls[0].headers['Referer'] == 'https://cardhobby.com.cn/Verify/Index'
    assert parse_qs(calls[0].content.decode(), keep_blank_values=True) == {
        'mobile': ['86 13800000000'], 'SecurityCode': [''], 'smsType': ['9']}
    assert asyncio.run(service.verification(conn, 'owner'))['sms_sent'] is True
    with pytest.raises(AccountError, match='稍后'):
        asyncio.run(service.send_sms(conn, 'owner'))
    assert len(calls) == 1


@pytest.mark.parametrize('payload', [{'status': '0', 'msg': 'private upstream text'}, {'status': True}, {}])
def test_failed_send_never_claims_sent_and_does_not_echo_remote_text(context, payload):
    service, conn, calls = context
    service.client_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)))
    with pytest.raises(AccountError) as error:
        asyncio.run(service.send_sms(conn, 'owner'))
    assert error.value.code == 'SMS_SEND_FAILED'
    assert 'private' not in str(error.value)
    assert asyncio.run(service.verification(conn, 'owner'))['sms_sent'] is False


def test_transport_failure_is_not_retried_and_remains_unsent(context):
    service, conn, calls = context

    def remote(request):
        calls.append(request)
        raise httpx.ReadTimeout('private', request=request)

    service.client_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with pytest.raises(AccountError):
        asyncio.run(service.send_sms(conn, 'owner'))
    assert len(calls) == 1
    assert asyncio.run(service.verification(conn, 'owner'))['sms_sent'] is False


def test_expired_challenge_cannot_send(context):
    service, conn, calls = context
    pending = service.cipher.decrypt('owner', 'pending', conn.value['pending_enc'])
    pending['expires'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    conn.value['pending_enc'] = service.cipher.encrypt('owner', 'pending', pending)
    with pytest.raises(AccountError) as error:
        asyncio.run(service.send_sms(conn, 'owner'))
    assert error.value.code == 'VERIFICATION_EXPIRED'
    assert not calls
