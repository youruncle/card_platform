"""Real order ledger with only the external image model replaced by observations."""
import asyncio
import os
from uuid import UUID

import pytest
import psycopg
from psycopg.rows import dict_row

from card_platform.config import get_settings
from card_platform.comparable_image_verifier import ComparableImageVerifier, VisualDecision, VisualCandidate
from card_platform.purchase_cost import OrderCapture
from card_platform.purchase_cost_service import PurchaseCostService
from test_purchase_cost_postgres import client as _postgres_client, register, order
from test_purchase_cost_recall_repair import image_correction

client = _postgres_client

pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ, reason='requires isolated PostgreSQL')


@pytest.mark.parametrize('verdict,expected', [
    ('same', 'recorded'), ('uncertain', 'unmatched'), ('mixed', 'unmatched'), ('text_retry', 'unmatched'),
    ('weak_text_retry', 'unmatched'),
    ('soft_conflict_retry', 'unmatched'), ('invalid_order_retry', 'unmatched'),
    ('compatible', 'unmatched'), ('weak_same', 'unmatched'), ('weak_geometry', 'unmatched'),
    ('missing_confirmation', 'unmatched'), ('weak_different', 'unmatched'),
    ('unbound_different', 'unmatched'), ('missing_target', 'unmatched'),
    ('multi_image', 'unmatched'), ('multi_quantity', 'unmatched'), ('no_image', 'unmatched'),
])
def test_mislabeled_order_flows_through_server_evidence_and_ledger(client, monkeypatch, verdict, expected):
    owner = register(client)
    created = client.post('/v1/portfolio/items', json=dict(owner_id=owner,
        card=dict(player_name='Darius Acuff Jr.',brand_name='Topps',product_name='Topps Now',season_start=2026)))
    assert created.status_code == 201, created.text
    item_id = UUID(created.json()['id'])
    (get_settings().resolved_upload_dir / 'fixture-front.jpg').write_bytes(b'fixture-image-only-model-is-mocked')

    retry_phase = False
    retry_modes = {'mixed','text_retry','weak_text_retry','soft_conflict_retry','invalid_order_retry'}

    async def observe(self, image, candidates, **kwargs):
        observations = {}
        for index, candidate in enumerate(candidates):
            result = image_correction()
            result['verdict'] = ('same' if index == 0 else 'uncertain') if verdict in retry_modes else ('same' if verdict in {'multi_image','multi_quantity','no_image','missing_target'} else verdict)
            if verdict in {'compatible', 'weak_same', 'weak_geometry', 'missing_confirmation', 'weak_different', 'unbound_different'}:
                result['verdict'] = 'same'
                if index > 0:
                    if verdict == 'compatible':
                        result['verdict'] = 'compatible'
                    elif verdict == 'weak_same':
                        result['confidence'] = '.89'
                    elif verdict == 'weak_geometry':
                        result['reasons'] = ['geometry_same_template:matches=30,inliers=20,ratio=0.667']
                    elif verdict == 'missing_confirmation':
                        result.pop('independent_confirmation')
                    else:
                        result['verdict'], result['confidence'] = 'different', '.40'
            if retry_phase:
                result['verdict'] = 'same' if index == 0 else 'different'
            if result['verdict'] == 'different':
                result['conflict_evidence'] = [dict(kind='card_text',source='images',both_visible=True,
                    target='Printed player Darius Acuff Jr.',candidate='Printed player Darryn Peterson')]
            result['candidate_id'] = candidate.id
            result['evidence_binding'].update(target_front_sha256=kwargs['identity']['image_content_hashes']['front'],
                                             target_back_sha256=None)
            from card_platform.comparable_image_verifier import visual_asset_binding
            expected_binding = visual_asset_binding(kwargs['identity'],candidate,b'',None,b'')
            result['evidence_binding'].update({key:expected_binding[key] for key in
                ('identity_sha256','candidate_title_sha256','source_url','candidate_image_url')})
            if verdict == 'unbound_different' and index > 0:
                result['evidence_binding']['target_front_sha256'] = 'f' * 64
            observations[candidate.id] = VisualDecision.model_validate(result)
        return observations
    monkeypatch.setattr(ComparableImageVerifier, 'verify', observe)

    async def run():
        nonlocal retry_phase
        async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'],row_factory=dict_row) as c:
            await c.execute('UPDATE collection_item SET image_front_url=%s WHERE id=%s AND owner_id=%s',
                            (None if verdict == 'missing_target' else '/uploads/fixture-front.jpg',item_id,owner))
            await c.commit()
            captured = order(title='2025 Topps Now Darryn Peterson 皮特森 Base')
            captured['lines'][0]['image_urls'] = ['https://images.cardhobby.com/fixture.jpg']
            captures = [OrderCapture.model_validate(captured)]
            if verdict in retry_modes | {'compatible','weak_same','weak_geometry','missing_confirmation','weak_different','unbound_different','multi_image','multi_quantity','no_image'}:
                second = order(item_id='900003',external_order_id='900004',quantity=2 if verdict == 'multi_quantity' else 1,title='2025 Topps Now Other Wrong Player Base')
                second['lines'][0]['image_urls'] = ([] if verdict == 'no_image' else ['https://images.cardhobby.com/second.jpg','https://images.cardhobby.com/back.jpg'] if verdict == 'multi_image' else ['https://images.cardhobby.com/second.jpg'])
                captures.append(OrderCapture.model_validate(second))
            result = await PurchaseCostService().resolve_search(c,UUID(owner),item_id,
                captures,True)
            await c.commit()
            ledger = await (await c.execute('SELECT unit_price,fee_amount FROM card_transaction WHERE collection_item_id=%s',(item_id,))).fetchall()
            if expected == 'recorded':
                assert result['recorded']['match_method'] == 'image_title_correction'
                assert len(ledger) == 1 and str(ledger[0]['unit_price']) == '10.00' and str(ledger[0]['fee_amount']) == '1.00'
                audit = await PurchaseCostService().audit(c,UUID(owner),item_id)
                assert audit['evidence']['title'] == '2025 Topps Now Darryn Peterson 皮特森 Base'
                assert audit['evidence']['visual_inference']['evidence_binding']['target_front_sha256']
            else:
                assert not ledger
                from card_platform.purchase_cost_service import FIELD_GATE_KEY
                gate = await (await c.execute('SELECT decision FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s',(owner,item_id,FIELD_GATE_KEY))).fetchone()
                assert gate and gate['decision']['status'] == 'incomplete'
                # Even a restored historical 'same' cannot bypass the persistent field gate.
                from card_platform.purchase_cost_inference import inference_key
                from card_platform.comparable_image_verifier import visual_asset_binding
                from psycopg.types.json import Jsonb
                if verdict == 'missing_target':
                    await c.execute('UPDATE collection_item SET image_front_url=%s WHERE id=%s',('/uploads/fixture-front.jpg',item_id))
                card = await PurchaseCostService().card(c,UUID(owner),item_id)
                source = await (await c.execute('SELECT capture,evidence_hash FROM cardhobby_purchase_order WHERE owner_id=%s AND external_order_id=%s',(owner,'900001'))).fetchone()
                line = source['capture']['lines'][0]
                unit_row = await (await c.execute('SELECT id FROM cardhobby_purchase_unit WHERE owner_id=%s AND item_id=%s',(owner,line['item_id']))).fetchone()
                unit = unit_row['id']
                historical = image_correction()
                historical['evidence_binding'].update(target_front_sha256=card['image_content_hashes']['front'],target_back_sha256=None,
                    identity_sha256=visual_asset_binding(card,VisualCandidate('', '', ''),b'',None,b'')['identity_sha256'])
                await c.execute('INSERT INTO cardhobby_cost_visual_inference(owner_id,collection_item_id,fingerprint,decision) VALUES(%s,%s,%s,%s) ON CONFLICT(owner_id,collection_item_id,fingerprint) DO UPDATE SET decision=EXCLUDED.decision',
                    (owner,item_id,inference_key(card,line,source['evidence_hash']),Jsonb(historical)))
                preview = await PurchaseCostService().candidates(c,UUID(owner),item_id)
                restored = next(row for row in preview['candidates'] if row['unit_id'] == str(unit))
                assert not restored['automatic'] and '购买候选尚未完整核验' in restored['missing']
            assert result['status'] == expected, result
            if verdict == 'mixed':
                retry_phase = True
                # A partial retry cannot forget the previously uncertain order.
                retry = await PurchaseCostService().resolve_search(c,UUID(owner),item_id,captures[:1],True)
                assert retry['status'] == 'unmatched', retry
                assert any(row['code'] == 'previous_candidate_scope_not_covered' for row in retry['verification_issues'])
                # Reobserve the complete field: one accepted and one ruled out.
                retry = await PurchaseCostService().resolve_search(c,UUID(owner),item_id,captures,True)
                assert retry['status'] == 'recorded', retry
                assert retry['recorded']['item_id'] == '900002'
                assert await (await c.execute('SELECT count(*) AS count FROM card_transaction WHERE collection_item_id=%s',(item_id,))).fetchone() == {'count':1}
                assert not await (await c.execute('SELECT decision FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s',(owner,item_id,FIELD_GATE_KEY))).fetchone()
            elif verdict == 'text_retry':
                corrected = [row.model_dump(mode='json') for row in captures]
                corrected[0]['lines'][0]['title'] = '2026 Topps Now Darius Acuff Jr Base'
                corrected[1]['lines'][0]['title'] = '2026 Panini Prizm Stephen Curry Base'
                corrected = [OrderCapture.model_validate(row) for row in corrected]
                retry = await PurchaseCostService().resolve_search(c,UUID(owner),item_id,corrected[:1],True)
                assert retry['status'] == 'unmatched', retry
                retry = await PurchaseCostService().resolve_search(c,UUID(owner),item_id,corrected,True)
                assert retry['status'] == 'recorded', retry
                assert not await (await c.execute('SELECT decision FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s',(owner,item_id,FIELD_GATE_KEY))).fetchone()
            elif verdict in {'weak_text_retry','soft_conflict_retry','invalid_order_retry'}:
                corrected = [row.model_dump(mode='json') for row in captures]
                corrected[0]['lines'][0]['title'] = '2026 Topps Now Darius Acuff Jr Base'
                if verdict == 'weak_text_retry':
                    corrected[1]['lines'][0]['title'] = 'Darius Acuff Jr'
                elif verdict == 'soft_conflict_retry':
                    corrected[1]['lines'][0]['title'] = '2025 Darryn Peterson'
                else:
                    corrected[1]['paid_total'] = '0.00'
                retry = await PurchaseCostService().resolve_search(c,UUID(owner),item_id,
                    [OrderCapture.model_validate(row) for row in corrected],True)
                assert retry['status'] == 'unmatched', retry
                assert await (await c.execute('SELECT decision FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s',(owner,item_id,FIELD_GATE_KEY))).fetchone()
    asyncio.run(run())
