import pytest
from scripts import audit_cardhobby_valuation as audit

@pytest.mark.asyncio
async def test_shadow_uses_frozen_identity_no_database_or_model(monkeypatch):
    import psycopg
    from card_platform.ingestion.cardhobby import CardHobbyProvider, CardHobbySearchResult
    monkeypatch.setattr(psycopg,'connect',lambda *a,**k: pytest.fail('shadow must not open database'))
    observed=[]
    async def direct(self,identity):
        assert self.client is None
        assert self.max_search_pages==1
        observed.append(identity['player_name'])
        return CardHobbySearchResult()
    monkeypatch.setattr(CardHobbyProvider,'_search_direct',direct)
    baseline={'items':[{'display_id':1,'collection_item_id':'a','player_name':'Player'}]}
    rows=await audit.shadow_retrieval(baseline, {'items':[{'display_id':1,'split':'development'}]},split='development',max_pages=1)
    assert observed==['Player']
    assert rows[0]['production_writeback'] is False
    assert rows[0]['reference_interval'] is None

@pytest.mark.asyncio
async def test_repeated_source_failure_stops_requests_and_records_unattempted_cards(monkeypatch):
    from card_platform.ingestion.cardhobby import CardHobbyProvider, CardHobbySearchResult, CardHobbyQueryDiagnostic
    calls=[]
    async def failed(self,identity):
        calls.append(1)
        return CardHobbySearchResult(query_diagnostics=[CardHobbyQueryDiagnostic(query='q',stage='s',outcome='error',error='unavailable')])
    monkeypatch.setattr(CardHobbyProvider,'_search_direct',failed)
    baseline={'items':[{'display_id':i,'collection_item_id':str(i)} for i in range(5)]}
    samples={'items':[{'display_id':i,'split':'development'} for i in range(5)]}
    rows=await audit.shadow_retrieval(baseline,samples,split='development')
    assert len(calls)==3
    assert len(rows)==5
    assert rows[-1]['blocked_reason']=='source_circuit_open'
