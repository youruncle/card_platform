"""Real ledger regression after a source-backed identity correction."""
import os
from decimal import Decimal

import pytest

from card_platform.config import get_settings
from card_platform.comparable_image_verifier import VisualDecision, ComparableImageVerifier
from test_purchase_cost_postgres import client as _client, register, order

client = _client
pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ, reason='requires isolated PostgreSQL')


@pytest.mark.parametrize('card,serial,title', [
    (dict(player_name='Falcon Rising',brand_name='Topps',season_start=2024,
          product_name='Star Wars Galaxy',card_number='93',parallel_name='Mojo'),38,
     'Falcon Rising Topps Star Wars Galaxy /50编 金mojo折射/瑕疵见详图/16161场均总/28'),
    (dict(player_name='Kasparas Jakucionis',brand_name='Topps',season_start=2025,season_end=2026,
          product_name='Topps Basketball',card_number='NS-20',insert_name='New School',parallel_name='Gold Rainbow'),10,
     'Kasparas Jakucionis 2025 Topps Flagship RC /50编 New School 金折折射/瑕疵见详图/16161场均总/43'),
])
def test_numbered_card_cost_uses_images_for_missing_seller_fields(client,monkeypatch,card,serial,title):
    owner = register(client)
    result = client.post('/v1/portfolio/items',json=dict(owner_id=owner,card=card,
        serial_number=serial,serial_total=50,is_numbered=True))
    assert result.status_code == 201, result.text
    item_id = result.json()['id']
    image = b'\xff\xd8\xfffixture'
    (get_settings().resolved_upload_dir/'subject-fixture.jpg').write_bytes(image)
    import asyncio
    import psycopg
    from psycopg.rows import dict_row

    async def bind_front():
        async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'],row_factory=dict_row) as conn:
            await conn.execute('UPDATE collection_item SET image_front_url=%s WHERE id=%s',('/uploads/subject-fixture.jpg',item_id))
            await conn.commit()

    asyncio.run(bind_front())

    from card_platform.purchase_cost_images import PurchaseImageObserver
    from purchase_cost_image_fixtures import paired_observation
    async def observe(self,identity,line,order_hash):
        return paired_observation(identity,line,order_hash)
    monkeypatch.setattr(PurchaseImageObserver,'observe',observe)
    captured = order(title=title)
    captured['lines'][0]['image_urls'] = ['https://images.cardhobby.com/fixture.jpg']
    result = client.post(f'/v1/purchase-cost/items/{item_id}/search-result',json={'orders':[captured],'complete':True})
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'recorded', result.json()
    assert result.json()['recorded']['match_method'] == 'multi_image_serial'
    assert Decimal(result.json()['recorded']['total_cost']) == Decimal('11.00')
    detail = client.get(f'/v1/portfolio/items/{item_id}/detail').json()
    assert len(detail['transactions']) == 1
    repeated = client.post(f'/v1/purchase-cost/items/{item_id}/search-result',json={'orders':[captured],'complete':True})
    assert repeated.json()['status'] == 'skipped'
