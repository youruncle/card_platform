"""A bounded cost operation must not repeat failed bundle model calls each round."""
import asyncio
import os
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row

from card_platform.config import get_settings
from card_platform.purchase_bundle_verifier import PurchaseBundleVerifier
from card_platform.purchase_cost import OrderCapture
from card_platform.purchase_cost_knowledge import purchase_cost_knowledge_scope
from card_platform.purchase_cost_service import PurchaseCostService
from test_purchase_cost_postgres import client as _postgres_client, order, register

client = _postgres_client
pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ,
                                reason='requires isolated PostgreSQL')


@pytest.mark.parametrize('retry', ['same_operation', 'new_operation', 'changed_image', 'child_task'])
@pytest.mark.parametrize('failure', ['exception', 'no_observation'])
def test_failed_bundle_attempt_is_bounded_by_operation_and_current_images(client, monkeypatch, retry, failure):
    owner = UUID(register(client))
    created = client.post('/v1/portfolio/items', json=dict(owner_id=str(owner),
        card=dict(player_name='Example Player',brand_name='Topps',product_name='Chrome',season_start=2025)))
    assert created.status_code == 201, created.text
    item = UUID(created.json()['id'])
    front = get_settings().resolved_upload_dir / 'bundle-attempt.jpg'
    front.write_bytes(b'first-target-image')
    value = order(title='2025 Topps Chrome Example Player 两张打包')
    value['lines'][0]['image_urls'] = ['https://images.cardhobby.com/bundle.jpg']
    orders = [OrderCapture.model_validate(value)]
    calls = []

    async def observe(self, card, line):
        calls.append(card['image_content_hashes']['front'])
        if failure == 'exception':
            raise ValueError('fixture model validation failure')
        return None

    monkeypatch.setattr(PurchaseBundleVerifier,'observe',observe)

    async def run():
        async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'],
                                                         row_factory=dict_row) as conn:
            await conn.execute('UPDATE collection_item SET image_front_url=%s WHERE id=%s',
                               ('/uploads/bundle-attempt.jpg',item))
            service = PurchaseCostService()
            await service.sync(conn,owner,orders)
            with purchase_cost_knowledge_scope():
                await service.prepare_bundle_inference(conn,owner,item,orders)
                await service.prepare_bundle_inference(conn,owner,item,orders)
                assert len(calls) == 1
                if retry == 'changed_image':
                    front.write_bytes(b'replacement-target-image')
                    await service.prepare_bundle_inference(conn,owner,item,orders)
                    assert len(calls) == 2 and calls[0] != calls[1]
                elif retry == 'child_task':
                    await asyncio.create_task(service.prepare_bundle_inference(conn,owner,item,orders))
                    assert len(calls) == 2
                    await service.prepare_bundle_inference(conn,owner,item,orders)
                    assert len(calls) == 2
            if retry == 'new_operation':
                with purchase_cost_knowledge_scope():
                    await service.prepare_bundle_inference(conn,owner,item,orders)
                assert len(calls) == 2
            # Skipping a repeated failure grants no observation or financial authority.
            assert not await (await conn.execute('''SELECT decision FROM cardhobby_cost_visual_inference
                WHERE owner_id=%s AND collection_item_id=%s''',(owner,item))).fetchall()
            assert not await (await conn.execute('SELECT id FROM card_transaction WHERE collection_item_id=%s',
                                                (item,))).fetchall()

    asyncio.run(run())
