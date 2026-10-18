from card_platform.valuation_worker import ComparableSearchResult, empty_valuation_recall_diagnostics
import pytest


@pytest.mark.asyncio
async def test_real_visual_batch_error_preserves_model_code_and_stage(tmp_path, monkeypatch):
    import cv2
    import httpx
    import numpy as np
    from decimal import Decimal
    from unittest.mock import AsyncMock
    import card_platform.comparable_image_verifier as module
    from card_platform.config import Settings
    content = cv2.imencode('.png', np.zeros((400,300,3),dtype=np.uint8))[1].tobytes()
    (tmp_path/'front.png').write_bytes(content)
    verifier = module.ComparableImageVerifier(Settings(upload_dir=str(tmp_path), vision_api_key=None))
    verifier.client = object()
    verifier._compare_batch = AsyncMock(side_effect=RuntimeError('AccessDenied.Unpurchased: model access denied'))
    verifier._geometric_template_score = lambda *_: (0,0,Decimal(0))
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request:httpx.Response(200,content=content,headers={'content-type':'image/png'}))
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs:original_client(transport=transport, **kwargs))
    result = await verifier.verify('/uploads/front.png', [module.VisualCandidate('listing','title','https://images.cardhobby.com/card.png')])
    reasons = result['listing'].reasons
    assert any('AccessDenied.Unpurchased' in reason and 'same_card' in reason for reason in reasons)


def test_model_access_denial_keeps_stage_and_raw_diagnostic():
    error = 'PermissionDeniedError: AccessDenied.Unpurchased: Access to model denied'
    result = empty_valuation_recall_diagnostics(ComparableSearchResult(
        notes=['市场来源刷新异常：cardhobby: ' + error],
        rejected_candidates=[{'reason_codes': ['player_conflict']}]))
    assert result['root_cause_codes'][0] == 'model_access_denied'
    assert result['upstream_failures'][0]['stage'] == 'model_access'
    assert error in result['upstream_failures'][0]['diagnostic']
    assert '卡淘没有记录' not in result['reason']


def test_cache_queries_are_observed_history_and_not_fresh_searches():
    search = ComparableSearchResult(
        search_execution='cache_replay',
        search_queries=['卡淘已售接口[cached]：example (召回20/通过0;rejected)'])
    result = empty_valuation_recall_diagnostics(search)
    assert result['observed_query_count'] == 1
    assert result['executed_query_count'] == 0
    assert result['search_execution'] == 'cache_replay'
