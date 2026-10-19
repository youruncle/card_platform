"""Verified choices survive an incomplete field; automatic writes do not."""
import asyncio
import os
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row

from card_platform.config import get_settings
from card_platform.purchase_cost_images import ObservationPending, PurchaseImageObserver
from card_platform.purchase_cost_service import FIELD_GATE_KEY, PurchaseCostService
from card_platform.repository import ConflictError
from purchase_cost_image_fixtures import paired_observation
from test_purchase_cost_multi_field_postgres import capture
from test_purchase_cost_postgres import client as _postgres_client, register

client = _postgres_client
pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ,
                                reason='requires isolated PostgreSQL')


@pytest.mark.parametrize('positive_count,expected', [(1,'unmatched'), (2,'needs_confirmation')])
@pytest.mark.parametrize('unresolved', ['uncertain','pending','missing_photo'])
def test_verified_multi_image_choices_keep_field_gate_until_explicit_selection(
    client, monkeypatch, positive_count, expected, unresolved,
):
    owner = UUID(register(client))
    created = client.post('/v1/portfolio/items', json=dict(owner_id=str(owner),
        card=dict(player_name='Example Player',brand_name='Topps',product_name='Chrome',
                  season_start=2025,card_number='Fixture-17'),
        serial_number=26,serial_total=75,is_numbered=True))
    assert created.status_code == 201, created.text
    item = UUID(created.json()['id'])
    (get_settings().resolved_upload_dir/'choices.jpg').write_bytes(b'fixture-observer-boundary')
    orders = [capture(index) for index in range(positive_count)]
    orders.append(capture(positive_count,images=unresolved!='missing_photo'))
    positive_ids = {'910002'} if positive_count==1 else {'910002','910003'}

    async def observe(self, card, line, order_hash):
        result = paired_observation(card,line,order_hash)
        if line['item_id'] not in positive_ids:
            if unresolved=='pending':
                raise ObservationPending('fixture budget exhausted')
            return {**result,'verdict':'uncertain','reasons':['printed_serial_unreadable']}
        return result

    monkeypatch.setattr(PurchaseImageObserver,'observe',observe)

    async def run():
        async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'],
                                                         row_factory=dict_row) as conn:
            await conn.execute('UPDATE collection_item SET image_front_url=%s WHERE id=%s',
                               ('/uploads/choices.jpg',item))
            service = PurchaseCostService()
            for _ in range(2):
                result = await service.resolve_search(conn,owner,item,orders,True)
                assert result['status']==expected, result
                assert result['verification_issues']
                if positive_count==2:
                    assert {r['item_id'] for r in result['candidates']}==positive_ids
                rows = await (await conn.execute('''SELECT fingerprint,decision FROM cardhobby_cost_visual_inference
                    WHERE owner_id=%s AND collection_item_id=%s''',(owner,item))).fetchall()
                gate = next(r['decision'] for r in rows if r['fingerprint']==FIELD_GATE_KEY)
                assert gate['status']=='incomplete'
                assert len(gate['pending_item_refs'])==positive_count+1
                assert sum(r['decision'].get('source')=='purchase_multi_image'
                           and r['decision'].get('verdict')=='same' for r in rows)==positive_count
                assert not await (await conn.execute('SELECT id FROM card_transaction WHERE collection_item_id=%s',
                                                    (item,))).fetchall()
            preview = await service.candidates(conn,owner,item)
            assert not any(row['automatic'] for row in preview['candidates'])
            choice = next(row for row in preview['candidates'] if row['item_id']=='910002')
            with pytest.raises(ConflictError):
                await service.confirm(conn,owner,item,UUID(choice['unit_id']),choice['evidence_hash'],automatic=True)
            assert not await (await conn.execute('SELECT id FROM card_transaction WHERE collection_item_id=%s',
                                                (item,))).fetchall()
            if positive_count==2:
                await service.confirm(conn,owner,item,UUID(choice['unit_id']),choice['evidence_hash'],automatic=False)
                assert await (await conn.execute('SELECT count(*) AS count FROM card_transaction WHERE collection_item_id=%s',
                                                (item,))).fetchone()=={'count':1}

    asyncio.run(run())
