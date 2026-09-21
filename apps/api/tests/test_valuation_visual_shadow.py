import pytest
from scripts import audit_cardhobby_valuation as audit

@pytest.mark.asyncio
async def test_visual_shadow_without_images_or_sources_is_explicit_unknown(tmp_path,monkeypatch):
    from card_platform.config import Settings
    import psycopg
    monkeypatch.setattr(psycopg,'connect',lambda *a,**k: pytest.fail('visual shadow opened database'))
    baseline={'frozen_at':'2026-09-21T00:00:00+08:00','items':[{'display_id':1,'collection_item_id':'a','card_definition_id':'d'}]}
    result=await audit.visual_shadow_card(baseline['items'][0],baseline,{'result':{'sales':[]}},Settings(vision_api_key=None))
    assert result['recognition_status']=='unavailable'
    assert result['same_card_status']=='unavailable'
    assert result['publication_eligible'] is False
    assert result['model_calls']==0
    assert result['model_cost'] is None
