"""Real DB and HTTP regression: blocked automatic costs must become visible."""
import asyncio
import os

import pytest
from psycopg.types.json import Jsonb

from test_auto_purchase_cost_postgres import connection
from test_purchase_cost_postgres import client, register, item
from test_cardhobby_account_postgres import remote
from test_cardhobby_account import CREDENTIALS

pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ, reason='requires migrated PostgreSQL')
PATH = '/v1/purchase-cost/automatic/attention'


def block(cards, *, code='VERIFICATION_REQUIRED', status='account_required'):
    async def write():
        async with await connection() as conn:
            await conn.execute("UPDATE automatic_purchase_cost SET status='completed',attempts=1,result=%s WHERE item_id=ANY(%s::uuid[])",
                               (Jsonb({'status': status, 'code': code}), cards))
    asyncio.run(write())


def test_attention_is_private_counts_only_actionable_account_blocks_and_never_logs_in(client, remote):
    assert client.get(PATH).status_code == 401
    first_owner = register(client)
    first_card = item(client, first_owner)
    block([first_card])
    owner = register(client)
    blocked = item(client, owner)
    manual = item(client, owner, purchase=dict(price='9', transacted_at='2026-01-01T00:00:00Z'))
    deleted = item(client, owner)
    network = item(client, owner)
    block([blocked, deleted])
    block([network], code='NETWORK_ERROR', status='error')
    async def write():
        async with await connection() as conn:
            await conn.execute("UPDATE collection_item SET deleted_at=now() WHERE id=%s", (deleted,))
            await conn.execute("INSERT INTO automatic_purchase_cost(item_id,owner_id,status,result) VALUES(%s,%s,'completed',%s)",
                               (manual, owner, Jsonb({'status':'account_required','code':'LOGIN_REQUIRED'})))
            await conn.execute("INSERT INTO cardhobby_account(owner_id,state) VALUES(%s,'verification_required')", (owner,))
    asyncio.run(write())
    response = client.get(PATH)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['blocked_count'] == 1
    assert value['code'] == 'VERIFICATION_REQUIRED'
    assert value['notice_key']
    assert value['notice_key'] == client.get(PATH).json()['notice_key']
    assert str(first_owner) not in response.text and first_card not in response.text
    assert not any(key in response.text for key in ['credentials_enc','session_enc','pending_enc'])
    assert remote.posts == []
    empty_owner = register(client)
    assert client.get(PATH).json()['blocked_count'] == 0
    assert client.get(PATH).json()['notice_key'] is None


def test_successful_login_requeues_only_own_account_blocks_and_preserves_transactions(client, remote):
    other = register(client)
    other_card = item(client, other)
    owner = register(client)
    retry = item(client, owner)
    with_transaction = item(client, owner)
    deleted = item(client, owner)
    network = item(client, owner)
    block([other_card, retry, with_transaction, deleted])
    block([network], code='NETWORK_ERROR', status='error')
    async def prepare():
        async with await connection() as conn:
            await conn.execute("INSERT INTO card_transaction(collection_item_id,transaction_type,transacted_at,quantity,unit_price,currency,fx_rate_to_base) VALUES(%s,'buy',now(),1,99,'CNY',1)", (with_transaction,))
            await conn.execute('UPDATE collection_item SET deleted_at=now() WHERE id=%s',(deleted,))
    asyncio.run(prepare())
    response = client.post('/v1/purchase-cost/cardhobby/login', json=CREDENTIALS)
    assert response.status_code == 200, response.text
    async def check():
        async with await connection() as conn:
            rows = await(await conn.execute('SELECT item_id,status,attempts,result FROM automatic_purchase_cost WHERE item_id=ANY(%s::uuid[])',
                                           ([other_card,retry,with_transaction,deleted,network],))).fetchall()
            values = {str(row['item_id']):row for row in rows}
            assert values[retry]['status'] == 'queued'
            assert values[retry]['attempts'] == 0
            for card in [other_card, with_transaction, deleted, network]:
                assert values[card]['status'] == 'completed'
            prices = await(await conn.execute('SELECT unit_price FROM card_transaction WHERE collection_item_id=%s',(with_transaction,))).fetchall()
            assert len(prices) == 1 and prices[0]['unit_price'] == 99
    asyncio.run(check())
    assert client.get(PATH).json()['blocked_count'] == 0


def test_failed_login_does_not_requeue_and_new_blocks_change_notice_key(client, remote):
    owner = register(client)
    first = item(client, owner)
    block([first], code='LOGIN_REQUIRED')
    initial = client.get(PATH)
    assert initial.status_code == 200, initial.text
    second = item(client, owner)
    block([second], code='LOGIN_REQUIRED')
    later = client.get(PATH).json()
    assert later['blocked_count'] == 2
    assert later['notice_key'] != initial.json()['notice_key']
    response = client.post('/v1/purchase-cost/cardhobby/login', json={**CREDENTIALS, 'password':'wrong'})
    assert response.status_code == 409
    assert client.get(PATH).json()['blocked_count'] == 2


def test_resolving_one_old_block_updates_count_without_creating_another_notice(client, remote):
    owner = register(client)
    first = item(client, owner)
    block([first])
    second = item(client, owner)
    block([second])
    initial = client.get(PATH).json()
    async def record_cost():
        async with await connection() as conn:
            await conn.execute("INSERT INTO card_transaction(collection_item_id,transaction_type,transacted_at,quantity,unit_price,currency,fx_rate_to_base) VALUES(%s,'buy',now(),1,99,'CNY',1)", (first,))
    asyncio.run(record_cost())
    later = client.get(PATH).json()
    assert later['blocked_count'] == 1
    assert later['notice_key'] == initial['notice_key']
