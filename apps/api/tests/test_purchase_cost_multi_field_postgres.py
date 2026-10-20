"""A scheduled multi-photo candidate must not remain its own weak competitor."""
import asyncio
import os
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

import card_platform.purchase_cost_service as purchase_service
from card_platform.config import get_settings
from card_platform.knowledge import KnowledgeResolver
from card_platform.purchase_cost import OrderCapture, evidence_hash, score_match
from card_platform.purchase_cost_images import PurchaseImageObserver, multi_review_eligible
from card_platform.purchase_cost_inference import inference_eligible
from card_platform.purchase_cost_service import PurchaseCostService
from purchase_cost_image_fixtures import paired_observation
from test_purchase_cost_postgres import client as _postgres_client, order, register

client = _postgres_client
postgres = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ,
                              reason='requires isolated PostgreSQL')


def numbered_identity():
    return dict(player_name='Example Player', brand_name='Topps', product_name='Chrome',
                season_start=2025, card_number='Fixture-17', serial_number=26,
                serial_total=75, is_numbered=True, image_front_url='/uploads/fixture.jpg')


def capture(index=0, *, quantity=1, images=True):
    value = order(item_id=str(910002 + index), external_order_id=str(920001 + index),
                  quantity=quantity, title='2025 Topps Chrome Example Player Base')
    value['lines'][0]['image_urls'] = ['https://images.cardhobby.com/fixture.jpg'] if images else []
    return OrderCapture.model_validate(value)


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class FieldConnection:
    def __init__(self, units):
        self.units = units

    async def execute(self, sql, args):
        if 'SELECT decision FROM cardhobby_cost_visual_inference' in sql:
            return Cursor([])
        assert 'SELECT u.id,u.item_id,o.external_order_id' in sql
        return Cursor([{**unit, "occupied": False} for unit in self.units])


@pytest.mark.asyncio
@pytest.mark.parametrize('competitor', ['none', 'missing_image', 'quantity', 'budget', 'target_missing'])
async def test_only_units_reaching_visual_review_leave_weak_competitors(monkeypatch, competitor):
    card = numbered_identity()
    orders = [capture()]
    if competitor == 'missing_image':
        orders.append(capture(1, images=False))
    elif competitor == 'quantity':
        orders.append(capture(1, quantity=2))
    elif competitor == 'budget':
        orders.extend(capture(index) for index in range(1, 7))
    elif competitor == 'target_missing':
        card['image_front_url'] = None
    knowledge = KnowledgeResolver()
    units, preview = [], []
    for source in orders:
        line = source.lines[0].model_dump(mode='json')
        match = score_match(card, line, knowledge)
        assert match['missing'] == ['限编']
        assert multi_review_eligible(card, match) and not inference_eligible(card, match)
        for index in range(line['quantity']):
            unit_id = uuid4()
            units.append(dict(id=unit_id, item_id=line['item_id'],
                              external_order_id=source.external_order_id))
            preview.append(dict(unit_id=str(unit_id), order_id=source.external_order_id,
                                evidence_hash=evidence_hash(source), **line, **match))
    observed = []
    service = PurchaseCostService()

    async def read_card(*args):
        return card

    async def candidates(*args, **kwargs):
        return dict(candidates=preview, rejected=[])

    async def load_knowledge(*args):
        return knowledge

    async def observe(conn, owner, item, identity, candidates, unresolved):
        observed.extend(row['unit_id'] for row in candidates)
        return dict(attempted=len(candidates), issues=unresolved)

    async def incomplete(*args):
        pass

    monkeypatch.setattr(service, 'card', read_card)
    monkeypatch.setattr(service, 'candidates', candidates)
    monkeypatch.setattr(service, 'observe_purchase_field', observe)
    monkeypatch.setattr(service, 'incomplete_field', incomplete)
    monkeypatch.setattr(purchase_service, 'load_purchase_cost_knowledge', load_knowledge)
    result = await service.prepare_visual_inference(FieldConnection(units), uuid4(), uuid4(),
                                                     orders, False)
    weak = [row for row in result['issues'] if row['code'] == 'candidate_identity_incomplete']
    if competitor == 'none':
        assert result['issues'] == []
        assert observed == [preview[0]['unit_id']]
    elif competitor in {'missing_image', 'quantity'}:
        # Even an eligible numbered competitor stays unresolved if its actual
        # line cannot be reviewed; do not filter merely on multi eligibility.
        assert observed == [preview[0]['unit_id']]
        assert len(weak) == (2 if competitor == 'quantity' else 1)
        assert {row['item_id'] for row in weak} == {orders[1].lines[0].item_id}
    elif competitor == 'budget':
        # Seven candidates now reach bounded durable review, rather than being
        # rejected as a whole. Each actual model call is budgeted by the observer.
        assert observed == [row['unit_id'] for row in preview]
        assert not weak
        assert not any(row['code'] == 'image_candidate_budget_exceeded' for row in result['issues'])
    else:
        assert not observed and result['attempted'] == 0
        assert len(weak) == (7 if competitor == 'budget' else 1)
        assert any(row['code'] == ('image_candidate_budget_exceeded' if competitor == 'budget'
                                   else 'target_image_missing') for row in result['issues'])


@postgres
@pytest.mark.parametrize('competitor', ['none', 'uncertain', 'missing_image', 'quantity', 'budget'])
def test_missing_title_serial_uses_reviewed_unit_without_erasing_other_competitors(
    client, monkeypatch, competitor,
):
    owner = register(client)
    created = client.post('/v1/portfolio/items', json=dict(owner_id=owner,
        card=dict(player_name='Example Player', brand_name='Topps', product_name='Chrome',
                  season_start=2025, card_number='Fixture-17'),
        serial_number=26, serial_total=75, is_numbered=True))
    assert created.status_code == 201, created.text
    item_id = created.json()['id']
    filename = f'multi-field-{uuid4().hex}.jpg'
    (get_settings().resolved_upload_dir / filename).write_bytes(b'synthetic-observer-boundary')

    async def bind_front():
        async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'],
                                                         row_factory=dict_row) as conn:
            await conn.execute('UPDATE collection_item SET image_front_url=%s WHERE id=%s',
                               ('/uploads/' + filename, UUID(item_id)))
            await conn.commit()
    asyncio.run(bind_front())
    observed = []

    async def observe(self, card, line, order_hash):
        observed.append(line['item_id'])
        result = paired_observation(card, line, order_hash)
        if competitor == 'uncertain' and line['item_id'] != '910002':
            result = {**result, 'verdict': 'uncertain', 'reasons': ['printed_serial_unreadable']}
        return result
    monkeypatch.setattr(PurchaseImageObserver, 'observe', observe)
    orders = [capture()]
    if competitor in {'missing_image', 'quantity', 'uncertain'}:
        orders.append(capture(1, quantity=2 if competitor == 'quantity' else 1,
                              images=competitor != 'missing_image'))
    elif competitor == 'budget':
        orders.extend(capture(index) for index in range(1, 7))
    response = client.post(f'/v1/purchase-cost/items/{item_id}/search-result',
                           json=dict(orders=[row.model_dump(mode='json') for row in orders],
                                     complete=True))
    assert response.status_code == 200, response.text
    result = response.json()
    detail = client.get(f'/v1/portfolio/items/{item_id}/detail').json()
    if competitor == 'none':
        assert result['status'] == 'recorded', result
        assert result['recorded']['match_method'] == 'multi_image_serial'
        assert result['recorded']['item_id'] == '910002'
        assert len(detail['transactions']) == 1
        repeated = client.post(f'/v1/purchase-cost/items/{item_id}/search-result',
                               json=dict(orders=[row.model_dump(mode='json') for row in orders],
                                         complete=True))
        assert repeated.json()['status'] == 'skipped'
    elif competitor == 'budget':
        assert result['status'] == 'needs_confirmation', result
        assert not detail['transactions']
        assert len(result['candidates']) == 7
    else:
        assert result['status'] == 'unmatched', result
        assert not detail['transactions']
        if competitor in {'missing_image', 'quantity'}:
            assert observed == ['910002']
            weak = [row for row in result['verification_issues']
                    if row['code'] == 'candidate_identity_incomplete']
            assert weak and {row['item_id'] for row in weak} == {'910003'}
