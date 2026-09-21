from collections import Counter
from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest
from card_platform.domain.comparables import _contains_player
from card_platform.domain.player_aliases import PLAYER_SEARCH_ALIASES, matching_player_aliases
from card_platform.ingestion.cardhobby import append_rejected_example, CardHobbySearchResult, _title_matches_player


@pytest.mark.parametrize('name,alias',[(name,alias) for name in PLAYER_SEARCH_ALIASES for alias in matching_player_aliases(name)])
def test_ingestion_alias_is_also_understood_by_valuation_classifier(name,alias):
    assert _contains_player(alias,name)


def test_shared_roster_keeps_each_subject_without_requiring_english_spelling():
    name='Jason Kidd, Jason Terry, Dirk Nowitzki'
    title='2017-18 Status Dirk Nowitzki 贾森基德 杰森特里 Factions 199编'
    assert _title_matches_player({'player_name':name},title)
    assert _contains_player(title,name)
    assert not _contains_player('2017-18 Status Dirk Nowitzki Factions 199编',name)


@pytest.mark.parametrize('name,title',[('Magic Johnson','澳大利亚魔术师'),('Jason Kidd','基德尔斯'),('LeBron James','布朗尼詹姆斯')])
def test_discovery_only_ambiguous_names_are_not_shared_identity_proof(name,title):
    assert not _contains_player(title,name)


def test_rejected_examples_are_bounded_audit_only_not_market_evidence():
    examples=[]
    sale=SimpleNamespace(item_id='123',source_url='https://cardhobby.com.cn/Market/Item/123',title='x'*1000,
        occurred_at=datetime.now(timezone.utc),sale_price=Decimal('2'),evidence_level='ended_auction',bid_count=1)
    for n in range(100):
        append_rejected_example(examples,sale,['same_reason',f'other_{n%10}'],-2)
    assert len(examples)==5
    assert max(Counter(r for e in examples for r in e['reasons']).values())<=2
    assert all(e['audit_only'] and len(e['title'])<=300 for e in examples)
    result=CardHobbySearchResult(query_diagnostics=[{'query':'example','stage':'test','outcome':'identity_gate_rejected','rejected_examples':examples}])
    assert not result.sales
