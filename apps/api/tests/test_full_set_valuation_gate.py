from decimal import Decimal
import pytest
from card_platform.domain.listing_title import is_multi_card_listing
from card_platform.domain.comparables import Classification, ComparableTier
from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity

@pytest.mark.parametrize('title',['Synthetic Player 字母特卡 全套','Synthetic Player 字母全套','Synthetic Player full set','Synthetic Player complete set'])
def test_complete_sets_are_always_multiple_cards(title):
    assert is_multi_card_listing(title, 'Donruss Elite')


def test_positive_visual_and_provider_cannot_authorize_a_full_set(monkeypatch):
    monkeypatch.setattr('card_platform.formal_cardhobby_gate.assess_cardhobby_candidate',lambda *_:(Decimal('.95'),[], 'exact_or_near_exact'))
    sale=dict(item_id='999999',title='2023 Synthetic Player 字母特卡 全套',source_url='https://cardhobby.com.cn/Market/Item/999999',occurred_at='2026-01-01T12:00:00+08:00',sale_price='300',bid_count=2,evidence_level='ended_auction',sale_type='auction',match_confidence='.95',sold_status_evidence='Status=-2 with bids')
    c=Classification(ComparableTier.T0,Decimal('.95'),Decimal('.7'),field_matches={'visual':True,'insert':True})
    result=enforce_formal_cardhobby_identity({},sale,c,visual_verdict='compatible')
    assert result.tier==ComparableTier.REJECTED
    assert 'multi_card_listing_not_single_card_evidence' in result.hard_conflicts


@pytest.mark.asyncio
async def test_normal_raw_card_calculation_must_run_final_source_gate(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    import card_platform.valuation_worker as module

    class GateReached(Exception):
        pass

    row = dict(id=uuid4(), source_code='cardhobby', source_url='https://example.test/sale',
               source_reliability=Decimal('.7'), grade_company=None, grade_value=None,
               listing_title='2023 Synthetic Product Fictional Player #12',
               raw_payload={}, provider_raw_payload={}, provider_identity_classification={})
    @asynccontextmanager
    async def context():
        yield connection
    connection = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchall=AsyncMock(return_value=[row]))),transaction=context)
    worker = module.AutomatedValuationWorker.__new__(module.AutomatedValuationWorker)
    worker.database = SimpleNamespace(connection=context)
    worker.knowledge_runtime = None
    monkeypatch.setattr(module, 'deduplicate_market_rows', lambda rows:(rows,[]))
    monkeypatch.setattr(module, 'build_identity_suggestions', lambda *_:[])
    monkeypatch.setattr(module.IdentityAuditRepository, 'replace_open_suggestions', AsyncMock())
    monkeypatch.setattr(module.ComparableClassifier, 'classify', lambda *_:Classification(ComparableTier.T0,Decimal('.95'),Decimal('.7')))
    monkeypatch.setattr(module,'revalidated_cardhobby_assessment', lambda *_:('exact_or_near_exact',()))
    monkeypatch.setattr(module,'reconcile_cardhobby_classification',lambda c,*args,**kwargs:(c,False))
    def gate(*args,**kwargs):
        raise GateReached()
    monkeypatch.setattr(module,'enforce_formal_cardhobby_identity',gate)
    identity=dict(collection_item_id=uuid4(),card_definition_id=uuid4(),player_name='Fictional Player',product_name='Synthetic Product',season_start=2023,card_number='12',grade_company=None,grade_value=None)
    search=SimpleNamespace(comparables=[SimpleNamespace(source_url=row['source_url'])])
    with pytest.raises(GateReached):
        await worker._calculate(uuid4(),identity,search)


@pytest.mark.parametrize('accepted,conflict,title,allowed', [
    (False, '标题未命中目标平行版本', '2023 Fictional Player', False),
    (True, '标题未命中目标平行版本', '2023 Fictional Player', False),
    (True, '标题明确为其他平行版本，与目标平行不一致', '2023 Fictional Player', False),
    (True, '标题未命中目标平行版本', '2023 Fictional Player 全套', False),
])
def test_persisted_review_only_supplies_missing_fields(monkeypatch, accepted, conflict, title, allowed):
    monkeypatch.setattr('card_platform.formal_cardhobby_gate.assess_cardhobby_candidate', lambda *_:(Decimal('.4'),[conflict],'same_player_product_analog'))
    sale=dict(item_id='999999',title=title,source_url='https://cardhobby.com.cn/Market/Item/999999',occurred_at='2026-01-01T12:00:00+08:00',sale_price='10',bid_count=2,evidence_level='ended_auction',sale_type='auction',match_confidence='.4',sold_status_evidence='Status=-2 with bids')
    classification=Classification(ComparableTier.T0,Decimal('1'),Decimal('.7'),field_matches={'parallel':True})
    result=enforce_formal_cardhobby_identity({'parallel_name':'Hyper Red and Green'},sale,classification,identity_accepted=accepted,visual_verdict='same')
    assert (result.tier==ComparableTier.T0) is allowed
