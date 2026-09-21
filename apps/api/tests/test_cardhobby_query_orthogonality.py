import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from card_platform.ingestion.cardhobby import CardHobbyProvider


@pytest.mark.parametrize('product,insert',[('Bowman Insider','Rewind: achievements spanning multiple years and all defensive teams'),('Example Basketball','NIL'),('An Incorrect Product Name','Draft Picks & Prospects')])
def test_one_query_survives_all_weak_catalog_fields(product,insert):
    plan=CardHobbyProvider.build_query_plan(dict(player_name='Example Person',season_start=2005,season_end=2006,product_name=product,insert_name=insert,card_number='TOP-LEFT',parallel_name='Silver'))
    assert ('player_release_recovery','Example Person 2005') in plan
    assert len(plan)<=12


def test_independent_recovery_does_not_displace_numbered_product_search():
    plan=CardHobbyProvider.build_query_plan(dict(player_name='LeBron James',season_start=2025,season_end=2026,product_name='National Treasures',insert_name='Treasured Threads',serial_total=25,card_number='TT-LBJ'))
    assert any(stage=='refine_print_run' and '25' in query for stage,query in plan)
    assert any(stage=='player_release_recovery' for stage,query in plan)


@pytest.mark.asyncio
@pytest.mark.parametrize('insert', ['Treasured Threads', 'NIL', 'An uncertain insert'])
async def test_runtime_knowledge_merge_preserves_independent_and_numbered_queries(insert):
    resolver = SimpleNamespace(
        entities={'available': True},
        aliases_for=lambda *_: (),
        build_query_plan=lambda *_: [SimpleNamespace(query='Example Knowledge Alias One'), SimpleNamespace(query='Example Knowledge Alias Two')],
    )
    provider = CardHobbyProvider(None, 'unused', resolver=resolver)
    provider._get_search_payload = AsyncMock(return_value={'data': {'PagedMarketItemList': []}})
    identity=dict(player_name='Example Player',season_start=2025,season_end=2026,product_name='National Treasures',insert_name=insert,serial_total=25,card_number='TT-EX')
    result = await provider._search_direct(identity)
    stages = [query.stage for query in result.query_diagnostics]
    assert 'refine_print_run' in stages
    assert 'player_release_recovery' in stages
    assert 'knowledge_alias' in stages
    assert len(stages) <= 12
