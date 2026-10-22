"""A financially blocked saved source cannot be replaced by another identical purchase."""
import os

import pytest

from test_purchase_cost_postgres import client as _client, order, register

client = _client
pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ, reason='isolated PostgreSQL required')


def create_card(client, owner, source):
    payload = dict(owner_id=owner, card=dict(player_name='Provenance Fixture', brand_name='Topps',
                                          product_name='Chrome', season_start=2025))
    if source:
        payload['notes'] = '订单逐项图像复核：来源 https://cardhobby.com.cn/market/item/' + source + '。'
    response = client.post('/v1/portfolio/items', json=payload)
    assert response.status_code == 201, response.text
    return response.json()['id']


def captured(item_id, order_id):
    return order(item_id=item_id, external_order_id=order_id, title='2025 Topps Chrome Provenance Fixture Base')


@pytest.mark.parametrize('source_state', ['financially_blocked', 'absent', 'eligible'])
def test_saved_source_never_falls_back_to_another_identical_purchase(client, source_state):
    owner = register(client)
    card = create_card(client, owner, '910002')
    source = captured('910002', '910001')
    other = captured('920002', '920001')
    if source_state == 'financially_blocked':
        source.update(discount='1.00', displayed_total='10.00', paid_total='10.00')
    orders = [other] if source_state == 'absent' else [source, other]
    response = client.post(f'/v1/purchase-cost/items/{card}/search-result', json=dict(orders=orders, complete=True))
    assert response.status_code == 200, response.text
    result = response.json()
    if source_state == 'eligible':
        assert result['status'] == 'recorded', result
        assert result['recorded']['item_id'] == '910002'
    else:
        assert result['status'] == 'unmatched', result
        assert client.get(f'/v1/purchase-cost/items/{card}/audit').json() is None
        assert client.get(f'/v1/portfolio/items/{card}/detail').json()['transactions'] == []
    preview = client.get(f'/v1/purchase-cost/items/{card}/candidates').json()
    assert all(row['item_id'] != '920002' for row in preview['candidates'])
    rejected = [row for row in preview['rejected'] if row['item_id'] == '920002']
    assert rejected and all(row.get('provenance_conflicts') for row in rejected)


def test_no_saved_source_preserves_normal_single_purchase_matching(client):
    owner = register(client)
    card = create_card(client, owner, None)
    response = client.post(f'/v1/purchase-cost/items/{card}/search-result',
                           json=dict(orders=[captured('930002', '930001')], complete=True))
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'recorded'


def test_manual_confirmation_cannot_replace_saved_source(client):
    import psycopg
    from psycopg.rows import dict_row

    owner = register(client)
    card = create_card(client, owner, '910002')
    response = client.post('/v1/purchase-cost/sync', json=dict(orders=[captured('920002', '920001')]))
    assert response.status_code == 200, response.text
    with psycopg.connect(os.environ['TEST_DATABASE_URL'], row_factory=dict_row) as conn:
        unit = conn.execute('''SELECT u.id,o.evidence_hash FROM cardhobby_purchase_unit u
            JOIN cardhobby_purchase_order o ON o.id=u.order_id
            WHERE u.owner_id=%s AND u.item_id='920002' ''', (owner,)).fetchone()
    response = client.post(f'/v1/purchase-cost/items/{card}/confirm',
                           json=dict(unit_id=str(unit['id']), evidence_hash=unit['evidence_hash']))
    assert response.status_code == 409, response.text
    assert client.get(f'/v1/portfolio/items/{card}/detail').json()['transactions'] == []
