"""Diagnostics must describe real service rejection without writing a cost."""
from test_purchase_cost_postgres import client as fixture_client, register, item, order, preview, confirm, pytestmark  # noqa: F401


client = fixture_client

def test_incomplete_shipping_returns_amount_layer_and_preserves_empty_ledger(client):
    owner=register(client)
    card=item(client,owner)
    capture=order()
    capture['shipping']=None
    response=client.post('/v1/purchase-cost/sync',json=dict(orders=[capture]))
    assert response.status_code==200,response.text
    result=preview(client,card)
    assert result['candidates']==[]
    assert result['diagnostics']['amount_blocked']==1
    assert result['rejected'][0]['automatic_identity']
    assert '运费' in result['reason'] and '同款购买记录' in result['reason']
    assert client.get(f'/v1/portfolio/items/{card}/detail').json()['transactions']==[]


def test_already_assigned_same_card_type_reports_allocation_without_stealing(client):
    owner=register(client)
    first=item(client,owner)
    second=item(client,owner)
    assert client.post('/v1/purchase-cost/sync',json=dict(orders=[order()])).status_code==200
    candidate=preview(client,first)['candidates'][0]
    assert confirm(client,first,candidate).status_code==200
    result=preview(client,second)
    assert result['candidates']==[]
    assert result['diagnostics']['allocation_blocked']==1
    assert '占用' in result['reason']
    assert client.get(f'/v1/portfolio/items/{second}/detail').json()['transactions']==[]
