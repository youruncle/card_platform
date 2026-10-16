"""ID 586: missing insert must not turn Roses into a base-card search."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.ingestion.cardhobby import CardHobbyProvider, assess_cardhobby_candidate
from card_platform.formal_cardhobby_gate import title_discloses_damage
from card_platform.valuation_worker import AutomatedValuationWorker

IDENTITY = dict(player_name='Tracy McGrady', brand_name='Topps',
                product_name='Signature Class', season_start=2026,
                season_end=None, card_number='R-5', insert_name=None,
                is_autograph=False, is_patch=False)


def resolve(identity):
    worker = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
    worker.knowledge_runtime = None
    return worker.resolve_identity_row(identity)


def test_missing_roses_insert_is_recovered_with_catalog_provenance():
    original = deepcopy(IDENTITY)
    result = resolve(IDENTITY)
    assert result['insert_name'] == 'Roses'
    assert result['evidence']['insert'][0]['source'] == 'catalog'
    assert 'topps' in result['evidence']['insert'][0]['source_url'].lower()
    assert result['parallel_name'] is None
    assert result['is_autograph'] is False
    assert IDENTITY == original
    queries = [q for _, q in CardHobbyProvider.build_query_plan(result)]
    assert '麦迪 Roses' in queries
    assert '麦迪 玫瑰' in queries
    assert len(queries) <= 12


@pytest.mark.parametrize('number', ['R-1', 'R-16', 'R-25'])
def test_recovery_is_for_checklist_family_not_collection_id(number):
    assert resolve({**IDENTITY, 'card_number': number})['insert_name'] == 'Roses'


@pytest.mark.parametrize('updates', [
    {'product_name': 'Chrome'}, {'brand_name': 'Panini'},
    {'season_start': 2024}, {'season_start': 2026, 'season_end': 2027},
    {'card_number': 'R-26'}, {'card_number': 'R-0'}, {'card_number': 'AR-5'},
    {'card_number': 'R-5/25'}, {'card_number': None},
])
def test_catalog_recovery_does_not_guess_outside_reviewed_scope(updates):
    assert resolve({**IDENTITY, **updates})['insert_name'] is None


def test_explicit_insert_and_numbered_parallel_survive():
    result = resolve({**IDENTITY, 'insert_name': 'Star Cast', 'parallel_name': 'Red',
                      'serial_total': 25})
    assert result['insert_name'] == 'Star Cast'
    assert result['parallel_name'] == 'Red'
    assert result['serial_total'] == 25


def test_user_listing_recalled_but_damage_is_still_disclosed():
    row = json.loads((Path(__file__).parent / 'fixtures/cardhobby/roses-122156847.json').read_text())
    sale = CardHobbyProvider.parse_market_item(row)
    assert sale is not None
    assert assess_cardhobby_candidate(IDENTITY, sale)[0] == 0
    assert assess_cardhobby_candidate(resolve(IDENTITY), sale)[0] >= Decimal('.70')
    assert sale.sale_price == Decimal('131.11')
    assert sale.shipping_price == Decimal('18')
    assert title_discloses_damage(sale.title)
    chinese_only = sale.model_copy(update={'title': sale.title.replace('Roses ', '')})
    assert assess_cardhobby_candidate(resolve(IDENTITY), chinese_only)[0] >= Decimal('.70')
    wrong_parallel = sale.model_copy(update={'title': sale.title + ' Red /25'})
    assert assess_cardhobby_candidate(resolve(IDENTITY), wrong_parallel)[0] == 0


@pytest.mark.parametrize('product', ['Signature Class', 'Topps Signature Class', 'Topps Signature Class Basketball'])
@pytest.mark.parametrize('season', [(2025, 2026), (2025, None), (2026, None)])
def test_reviewed_release_spellings(product, season):
    result = resolve({**IDENTITY, 'product_name': product,
                      'season_start': season[0], 'season_end': season[1]})
    assert result['insert_name'] == 'Roses'


def test_physical_insert_evidence_prevents_catalog_backfill():
    result = CardIdentityResolver().resolve(IDENTITY, [
        {'source': 'physical_back', 'fields': {'insert': 'Star Cast'}},
    ])
    assert result.insert == 'Star Cast'


def test_damaged_user_listing_remains_excluded_from_formal_normal_condition_price():
    from card_platform.domain.comparables import Classification, ComparableTier
    from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity

    row = json.loads((Path(__file__).parent / 'fixtures/cardhobby/roses-122156847.json').read_text())
    sale = CardHobbyProvider.parse_market_item(row)
    result = enforce_formal_cardhobby_identity(
        resolve(IDENTITY), sale.model_dump(mode='json'),
        Classification(ComparableTier.T0, Decimal('.99'), Decimal('.75')),
        visual_verdict='same',
    )
    assert result.tier == ComparableTier.REJECTED
    assert 'disclosed_condition_damage_requires_separate_valuation' in result.hard_conflicts
