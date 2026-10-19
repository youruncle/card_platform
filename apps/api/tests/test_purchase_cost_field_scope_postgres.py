"""Pending visual scope belongs to products, never all products in an order."""
import asyncio
import os
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from card_platform.config import get_settings
from card_platform.comparable_image_verifier import ComparableImageVerifier
from card_platform.purchase_cost import OrderCapture
from card_platform.purchase_cost_images import PurchaseImageObserver
from card_platform.purchase_cost_service import FIELD_GATE_KEY, PurchaseCostService
from purchase_cost_image_fixtures import paired_observation
from test_purchase_cost_multi_field_postgres import capture
from test_purchase_cost_postgres import client as _postgres_client, register

client = _postgres_client
pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ,
                                reason='requires isolated PostgreSQL')


@pytest.mark.parametrize('change', ['unrelated', 'lost_text', 'missing_image', 'removed', 'legacy'])
def test_pending_scope_does_not_expand_or_forget_former_products(client, monkeypatch, change):
    owner = UUID(register(client))
    created = client.post('/v1/portfolio/items', json=dict(owner_id=str(owner),
        card=dict(player_name='Example Player', brand_name='Topps', product_name='Chrome',
                  season_start=2025, card_number='Fixture-17'),
        serial_number=26, serial_total=75, is_numbered=True))
    assert created.status_code == 201, created.text
    item = UUID(created.json()['id'])
    (get_settings().resolved_upload_dir / 'scope.jpg').write_bytes(b'fixture-observer-boundary')
    source = capture().model_dump(mode='json')
    if change != 'legacy':
        second = capture(1).lines[0].model_dump(mode='json')
        if change == 'unrelated':
            second['title'] = '2025 Stephon Castle'
        source['lines'].append(second)
        source['displayed_total'] = '21.00'
    retry = False

    async def observe(self, card, line, order_hash):
        evidence = paired_observation(card, line, order_hash)
        if not retry:
            evidence = {**evidence, 'verdict': 'uncertain', 'reasons': ['printed_serial_unreadable']}
        return evidence

    monkeypatch.setattr(PurchaseImageObserver, 'observe', observe)

    async def run():
        nonlocal retry
        async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'],
                                                         row_factory=dict_row) as conn:
            await conn.execute('UPDATE collection_item SET image_front_url=%s WHERE id=%s',
                               ('/uploads/scope.jpg', item))
            service = PurchaseCostService()
            if change == 'legacy':
                await conn.execute('''INSERT INTO cardhobby_cost_visual_inference
                    (owner_id,collection_item_id,fingerprint,decision) VALUES(%s,%s,%s,%s)''',
                    (owner,item,FIELD_GATE_KEY,Jsonb(dict(source='cost_candidate_field',
                        status='incomplete', pending_order_ids=[source['external_order_id']]))))
            first = await service.resolve_search(conn, owner, item, [OrderCapture.model_validate(source)], True)
            assert first['status'] == 'unmatched', first
            first_gate = await (await conn.execute('''SELECT decision FROM cardhobby_cost_visual_inference
                WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s''',
                (owner,item,FIELD_GATE_KEY))).fetchone()
            if change == 'lost_text':
                source['lines'][1]['title'] = 'Example Player'
            elif change == 'missing_image':
                source['lines'][1]['image_urls'] = []
            elif change == 'removed':
                source['lines'].pop()
                source['displayed_total'] = '11.00'
            retry = True
            result = await service.resolve_search(conn, owner, item, [OrderCapture.model_validate(source)], True)
            rows = await (await conn.execute('SELECT id FROM card_transaction WHERE collection_item_id=%s',
                                            (item,))).fetchall()
            if change == 'unrelated':
                assert result['status'] == 'recorded', result
                assert result['recorded']['item_id'] == '910002'
                assert len(rows) == 1
                # The unrelated product must never enter the persisted field.
                assert first_gate['decision'].get('pending_item_refs') == [
                    dict(order_id='920001',item_id='910002')]
            else:
                assert result['status'] == 'unmatched', result
                assert rows == []
                gate = await (await conn.execute('''SELECT decision FROM cardhobby_cost_visual_inference
                    WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s''',
                    (owner,item,FIELD_GATE_KEY))).fetchone()
                assert gate
                if change == 'legacy':
                    assert gate['decision'].get('legacy_pending_order_ids') == ['920001']
                    assert any(row['code'] == 'legacy_candidate_scope_unresolved'
                               for row in result['verification_issues'])
                else:
                    assert dict(order_id='920001',item_id='910003') in gate['decision']['pending_item_refs']
            await conn.commit()

    asyncio.run(run())


@pytest.mark.parametrize('failed', [False, True])
def test_legacy_title_incomplete_scope_diagnostic_preserves_new_refs(client, monkeypatch, failed):
    owner = UUID(register(client))
    created = client.post('/v1/portfolio/items', json=dict(owner_id=str(owner),
        card=dict(player_name='Example Player',brand_name='Topps',product_name='Chrome',season_start=2025)))
    assert created.status_code == 201, created.text
    item = UUID(created.json()['id'])

    async def verify(*args, **kwargs):
        if failed:
            raise ValueError('fixture external failure')
        return {}

    monkeypatch.setattr(ComparableImageVerifier,'verify',verify)

    async def run():
        async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'],
                                                         row_factory=dict_row) as conn:
            service = PurchaseCostService()
            await service.incomplete_field(conn,owner,item,{'older-order'},
                [dict(order_id='older-order',item_id='older-product')])
            result = await service.observe_legacy_title_field(conn,owner,item,
                dict(image_front_url='/uploads/scope.jpg'),
                [dict(unit_id='fixture-unit',order_id='current-order',item_id='current-product',
                      title='Fixture',image_urls=['https://images.cardhobby.com/fixture.jpg'])],
                [dict(code='previous_candidate_scope_not_covered')],{'current-order'})
            assert result['issues']
            gate = await (await conn.execute('''SELECT decision FROM cardhobby_cost_visual_inference
                WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s''',
                (owner,item,FIELD_GATE_KEY))).fetchone()
            assert gate['decision']['pending_item_refs'] == [
                dict(order_id='current-order',item_id='current-product'),
                dict(order_id='older-order',item_id='older-product')]
            assert gate['decision']['pending_order_ids'] == ['current-order','older-order']
            assert not await (await conn.execute('SELECT id FROM card_transaction WHERE collection_item_id=%s',
                                                (item,))).fetchall()

    asyncio.run(run())
