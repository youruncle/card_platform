import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
import httpx
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from card_platform import cardhobby_account_api as api
from card_platform.cardhobby_account import CardHobbyAccountService
from card_platform.cardhobby_account_crypto import AccountCipher
from test_cardhobby_account import CREDENTIALS, Remote
from test_purchase_cost_postgres import client as postgres_client, register as register_fixture

@pytest.fixture
def client(tmp_path, monkeypatch):
    owners = []
    try:
        for value in postgres_client.__wrapped__(tmp_path, monkeypatch):
            value.cardhobby_fixture_owners = owners
            yield value
    finally:
        async def cleanup():
            async with await AsyncConnection.connect(os.environ['TEST_DATABASE_URL']) as conn:
                for owner in owners:
                    await conn.execute('DELETE FROM app_user WHERE id=%s', (owner,))
        asyncio.run(cleanup())


def register(client):
    owner = register_fixture(client)
    client.cardhobby_fixture_owners.append(owner)
    return owner

pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ, reason='requires migrated PostgreSQL')
BASE = '/v1/purchase-cost/cardhobby'


@pytest.fixture
def remote(monkeypatch, tmp_path):
    remote = Remote()
    service = CardHobbyAccountService(AccountCipher(str(tmp_path/'key')), remote.client)
    monkeypatch.setattr(api, 'account_service', lambda: service)
    return remote


def row(owner, sql=None):
    async def read():
        async with await AsyncConnection.connect(os.environ['TEST_DATABASE_URL'], row_factory=dict_row) as conn:
            if sql:
                await conn.execute(sql, (owner,))
            return await (await conn.execute('SELECT * FROM cardhobby_account WHERE owner_id=%s', (owner,))).fetchone()
    return asyncio.run(read())


def test_account_authentication_and_private_validation(client, remote):
    assert client.get(BASE+'/status').status_code == 401
    owner = register(client)
    invalid = client.post(BASE+'/login', json={'username':'test','password':{'secret':'must-not-echo'}})
    assert invalid.status_code == 422 and 'must-not-echo' not in invalid.text
    assert row(owner) is None
    assert client.post(BASE+'/search', json={'keywords':['Test'], 'owner_id':owner}).status_code == 422


def test_bind_encrypted_reuse_expired_session_relogin_and_forget(client, remote):
    owner = register(client)
    assert client.get(BASE+'/status').json() == {'saved':False, 'state':'unbound'}
    assert client.post(BASE+'/search', json={'keywords':['Test']}).json()['detail']['code'] == 'LOGIN_REQUIRED'
    result = client.post(BASE+'/login', json=CREDENTIALS)
    assert result.status_code == 200 and result.json()['saved'], result.text
    assert CREDENTIALS['password'] not in str(row(owner))
    assert CREDENTIALS['username'] not in str(row(owner))
    assert row(owner)['session_enc']
    for _ in range(2):
        assert client.post(BASE+'/search', json={'keywords':['Test']}).json() == {'orders':[], 'complete':True}
    assert len(remote.posts) == 1
    remote.session = 'renewed'
    assert client.post(BASE+'/search', json={'keywords':['Test']}).status_code == 200
    assert len(remote.posts) == 2
    assert client.delete(BASE+'/account').status_code == 204
    assert row(owner) is None


def test_wrong_password_not_saved_and_no_automatic_retry_storm(client, remote):
    owner = register(client)
    failed = client.post(BASE+'/login', json={**CREDENTIALS,'password':'wrong'})
    assert failed.json()['detail']['code'] == 'LOGIN_REJECTED'
    assert row(owner)['credentials_enc'] is None
    assert row(owner)['pending_enc'] is None
    assert client.post(BASE+'/login', json=CREDENTIALS).json()['detail']['code'] == 'RATE_LIMITED'
    assert len(remote.posts) == 1


def test_accounts_are_owner_isolated(client, remote):
    owner1 = register(client)
    assert client.post(BASE+'/login', json=CREDENTIALS).status_code == 200
    client.cookies.clear()
    owner2 = register(client)
    assert client.get(BASE+'/status').json()['saved'] is False
    assert client.post(BASE+'/search', json={'keywords':['Test']}).json()['detail']['code'] == 'LOGIN_REQUIRED'
    assert client.delete(BASE+'/account').status_code == 204
    assert row(owner1)['credentials_enc'] and row(owner2) is None
    assert len(remote.posts) == 1


def test_verification_only_persists_credentials_after_success(client, remote):
    owner = register(client)
    remote.challenge = True
    challenge = client.post(BASE+'/login', json=CREDENTIALS)
    assert challenge.json()['detail']['code'] == 'VERIFICATION_REQUIRED'
    assert row(owner)['credentials_enc'] is None and row(owner)['pending_enc']
    assert client.post(BASE+'/search', json={'keywords':['Test']}).json()['detail']['code'] == 'VERIFICATION_REQUIRED'
    assert len(remote.posts) == 1
    verified = client.post(BASE+'/verify', json={'code':'123456'})
    assert verified.status_code == 200, verified.text
    assert row(owner)['credentials_enc'] and row(owner)['pending_enc'] is None


def test_failed_verification_cooldown_and_bounded_attempts(client, remote):
    owner = register(client)
    remote.challenge = True
    client.post(BASE+'/login', json=CREDENTIALS)
    for attempt in range(3):
        row(owner, 'UPDATE cardhobby_account SET retry_after=NULL WHERE owner_id=%s')
        result = client.post(BASE+'/verify', json={'code':'000000'})
        assert result.json()['detail']['code'] == ('LOGIN_REJECTED' if attempt == 2 else 'VERIFICATION_REQUIRED')
        assert client.post(BASE+'/verify', json={'code':'000000'}).json()['detail']['code'] == 'RATE_LIMITED'
    assert row(owner)['pending_enc'] is None and row(owner)['credentials_enc'] is None


def test_bad_page_preserves_credentials_and_does_not_relogin(client, remote):
    owner = register(client)
    client.post(BASE+'/login', json=CREDENTIALS)
    before = row(owner)['credentials_enc']
    remote.broken = True
    response = client.post(BASE+'/search', json={'keywords':['Test']})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'UNAVAILABLE'
    assert row(owner)['credentials_enc'] == before and len(remote.posts) == 1


def test_concurrent_expired_searches_only_login_once(client, remote):
    register(client)
    client.post(BASE+'/login', json=CREDENTIALS)
    remote.session = 'renewed'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(BASE+'/search', json={'keywords':['Test']}), range(2)))
    assert all(result.status_code == 200 for result in results)
    assert len(remote.posts) == 2


def test_saved_password_failure_pauses_automatic_login(client, remote):
    owner = register(client)
    client.post(BASE+'/login', json=CREDENTIALS)
    service = api.account_service()
    async def expire():
        async with await AsyncConnection.connect(os.environ['TEST_DATABASE_URL']) as conn:
            await conn.execute('UPDATE cardhobby_account SET credentials_enc=%s, session_enc=NULL WHERE owner_id=%s',
                               (service.cipher.encrypt(owner, 'credentials', {**CREDENTIALS,'password':'old-password'}), owner))
    asyncio.run(expire())
    assert client.post(BASE+'/search', json={'keywords':['Test']}).json()['detail']['code'] == 'LOGIN_REJECTED'
    for _ in range(2):
        assert client.post(BASE+'/search', json={'keywords':['Test']}).json()['detail']['code'] == 'LOGIN_REQUIRED'
    assert len(remote.posts) == 2


def test_network_failure_does_not_erase_binding_or_force_password_login(client, remote):
    owner = register(client)
    assert client.post(BASE+'/login', json=CREDENTIALS).status_code == 200
    before = row(owner)
    original = remote.handle
    def offline(request):
        raise httpx.ReadTimeout('secret-should-not-appear', request=request)
    remote.handle = offline
    failed = client.post(BASE+'/search', json={'keywords':['Test']})
    assert failed.status_code == 503 and failed.json()['detail']['code'] == 'NETWORK_ERROR'
    assert '检查登录状态' in failed.text and 'secret-should-not-appear' not in failed.text
    after = row(owner)
    assert after['credentials_enc'] == before['credentials_enc']
    assert after['session_enc'] == before['session_enc'] and after['state'] == 'connected'
    remote.handle = original
    assert client.post(BASE+'/search', json={'keywords':['Test']}).status_code == 200
    assert len(remote.posts) == 1
