import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import UUID, uuid4

import httpx
from PIL import Image
from pydantic import ValidationError
import pytest
import pytest_asyncio

from card_platform.config import Settings
from card_platform.mcp.schemas import McpFailure
from test_mcp_mutations import mutation_case as shared_mutation_case, revision

mutation_case = shared_mutation_case
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def research_case(mutation_case, tmp_path):
    from card_platform.mcp.research import ResearchTools
    tools, principal, ids = mutation_case
    root = tmp_path/'uploads'
    root.mkdir()
    for side, color in [('front', 'red'), ('back', 'blue')]:
        Image.new('RGB', (80,120), color).save(root/f'{side}.png')
    async with tools.database.connection() as conn:
        await conn.execute("UPDATE collection_item SET image_front_url='/uploads/front.png',image_back_url='/uploads/back.png' WHERE id=%s", (ids[0],))
        await conn.execute("UPDATE card_definition SET parallel_name='Base' WHERE id=(SELECT card_definition_id FROM collection_item WHERE id=%s)", (ids[0],))
    research = ResearchTools(tools.database, Settings(upload_dir=str(root)), tools.operations)
    return research, tools, principal, ids[0]


async def start(case, key=None):
    from card_platform.mcp.research import StartCardResearchRequest
    research, tools, principal, item_id = case
    return await research.start_card_research(principal, StartCardResearchRequest(item_id=item_id,
        expected_revision=await revision(tools, principal, item_id), idempotency_key=key or uuid4()))


async def test_new_research_is_durable_idempotent_and_not_old_prices(research_case):
    research, tools, principal, item_id = research_case
    key = uuid4()
    first = await start(research_case, key)
    assert (await start(research_case, key))['run_id'] == first['run_id']
    assert first['status'] == 'queued'
    assert first['fair_value'] is None
    async with tools.database.connection() as conn:
        row = await (await conn.execute('SELECT * FROM chatgpt_research_run WHERE id=%s', (UUID(first['run_id']),))).fetchone()
        assert row['packet']['searches'] == []
        assert row['packet']['front_sha256'] == hashlib.sha256((research.root.parent/'front.png').read_bytes()).hexdigest()
    other = principal.model_copy(update={"owner_id": uuid4()})
    with pytest.raises(McpFailure):
        await research.get_result(other, UUID(first['run_id']))


async def test_worker_captures_both_blocked_searches_and_preserves_null(research_case):
    from card_platform.mcp.research import ResearchWorker
    research, _, principal, _ = research_case
    run = await start(research_case)
    def network(request):
        if request.url.host == 'www.ebay.com':
            return httpx.Response(403, text='Access denied')
        raise httpx.ReadTimeout('synthetic timeout')
    worker = ResearchWorker(research, transport=httpx.MockTransport(network))
    await worker.process_one(UUID(run['run_id']))
    result = await research.get_result(principal, UUID(run['run_id']))
    assert result['status'] == 'review_required'
    assert result['fair_value'] is None
    assert {s['platform'] for s in result['packet']['searches']} == {'ebay','cardhobby'}
    assert all(s['capture_ref'] and s['diagnostic'] for s in result['packet']['searches'])
    assert {s['outcome'] for s in result['packet']['searches']} == {'blocked','error'}
    assert '已完成' not in json.dumps(result, ensure_ascii=False)


async def test_valid_empty_searches_satisfy_search_attempt_contract(research_case):
    from card_platform.mcp.research import ResearchWorker
    research, _, principal, _ = research_case
    run = await start(research_case)

    def network(request):
        if request.url.host == 'www.ebay.com':
            return httpx.Response(200, text='<html>0 results for this query</html>')
        return httpx.Response(200, json={'result': 1, 'data': {
            'PagedMarketItemList': [], 'Total': 0, 'TotalCount': 0, 'PageIndex': 1}})

    await ResearchWorker(research, transport=httpx.MockTransport(network)).process_one(UUID(run['run_id']))
    result = await research.get_result(principal, UUID(run['run_id']))
    assert {s['outcome'] for s in result['packet']['searches']} == {'no_results'}
    assert {s['status'] for s in result['assessment']['markets'].values()} == {'no_results'}
    assert not any(r.endswith('_history_search_not_executed') for r in result['assessment']['reasons'])
    assert result['fair_value'] is None


async def test_review_cannot_relabel_old_search_or_invent_missing_captures(research_case):
    from card_platform.mcp.research import ResearchWorker
    research, _, principal, _ = research_case
    run = await start(research_case)
    await ResearchWorker(research, transport=httpx.MockTransport(lambda r:httpx.Response(403,text='blocked'))).process_one(UUID(run['run_id']))
    result = await research.get_result(principal, UUID(run['run_id']))
    packet = copy.deepcopy(result['packet'])
    packet['searches'][0]['executed_at'] = (datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    with pytest.raises(McpFailure):
        await research.save_draft(principal.owner_id, UUID(run['run_id']), packet)
    packet = copy.deepcopy(result['packet'])
    packet['sales'] = [{'platform':'ebay','url':'https://127.0.0.1/itm/123','card_images':[]}]
    with pytest.raises(McpFailure):
        await research.save_draft(principal.owner_id, UUID(run['run_id']), packet)


async def test_review_hash_binding_and_unreviewed_results_never_publish(research_case):
    from card_platform.mcp.research import PrepareResearchPublicationRequest, ResearchWorker
    research, tools, principal, item_id = research_case
    run = await start(research_case)
    request = PrepareResearchPublicationRequest(run_id=run['run_id'], expected_revision=await revision(tools, principal,item_id), idempotency_key=uuid4())
    with pytest.raises(McpFailure):
        await research.prepare_research_publication(principal, request)
    await ResearchWorker(research, transport=httpx.MockTransport(lambda r:httpx.Response(403,text='blocked'))).process_one(UUID(run['run_id']))
    result = await research.get_result(principal, UUID(run['run_id']))
    reviewed = await research.approve_review(principal.owner_id, UUID(run['run_id']), result['evidence_digest'], uuid4())
    assert reviewed['status'] == 'insufficient_evidence'
    assert reviewed['fair_value'] is None
    with pytest.raises(McpFailure):
        await research.prepare_research_publication(principal, request)
    with pytest.raises(ValidationError):
        PrepareResearchPublicationRequest(**{**request.model_dump(), 'price':1000})
    with pytest.raises(McpFailure):
        await research.approve_review(principal.owner_id, UUID(run['run_id']), '0'*64, uuid4())


async def test_research_review_api_requires_session_origin_exact_digest_and_human_ack(research_case):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from card_platform.auth import AuthenticatedUser,current_user
    from card_platform.mcp.research_api import router
    from card_platform.mcp.research import ResearchWorker
    research,_,principal,_=research_case
    run=await start(research_case)
    await ResearchWorker(research,transport=httpx.MockTransport(lambda r:httpx.Response(403,text='blocked'))).process_one(UUID(run['run_id']))
    app=FastAPI()
    app.state.chatgpt_mcp_server=SimpleNamespace(research_tools=research)
    app.include_router(router)
    path=f"/v1/chatgpt/research/{run['run_id']}"
    user=AuthenticatedUser(id=principal.owner_id,session_id=uuid4(),email='research@example.invalid',display_name='Research',base_currency='CNY',role='member')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://cardsp.cn') as client:
        assert (await client.get(path)).status_code==401
        app.dependency_overrides[current_user]=lambda:user
        result=(await client.get(path)).json()
        assert result['captures']
        assert (await client.get(result['captures'][0]['url'])).headers['content-type'].startswith('text/plain')
        body={'evidence_digest':result['evidence_digest'],'reviewed_all_candidates':True}
        assert (await client.post(path+'/review',json=body)).status_code==403
        headers={'Origin':'https://cardsp.cn'}
        assert (await client.post(path+'/review',headers=headers,json={**body,'reviewed_all_candidates':False})).status_code==422
        assert (await client.post(path+'/review',headers=headers,json={**body,'evidence_digest':'0'*64})).status_code==409
        assert (await client.post(path+'/review',headers=headers,json=body)).status_code==200
        app.dependency_overrides[current_user]=lambda:AuthenticatedUser(**{**user.__dict__,'id':uuid4()})
        assert (await client.get(path)).status_code==404
        assert (await client.get(result['captures'][0]['url'])).status_code==404


async def test_candidate_capture_queue_survives_chat_and_only_finishes_after_worker(research_case):
    from card_platform.mcp.research import ResearchWorker
    research,_,principal,_=research_case
    run=await start(research_case)
    await ResearchWorker(research,transport=httpx.MockTransport(lambda r:httpx.Response(403,text='blocked'))).process_one(UUID(run['run_id']))
    previous=await research.get_result(principal,UUID(run['run_id']))
    queued=await research.queue_draft(principal.owner_id,UUID(run['run_id']),previous['packet'])
    assert queued['status']=='queued'
    resumed=await research.get_result(principal,UUID(run['run_id']))
    assert resumed['status']=='queued'
    await ResearchWorker(research).process_one(UUID(run['run_id']))
    assert (await research.get_result(principal,UUID(run['run_id'])))['status']=='review_required'


async def test_revoked_connection_stops_queued_candidate_capture(research_case):
    from card_platform.mcp.research import ResearchWorker
    research,_,principal,_=research_case
    run=await start(research_case)
    await ResearchWorker(research,transport=httpx.MockTransport(lambda r:httpx.Response(403,text='blocked'))).process_one(UUID(run['run_id']))
    previous=await research.get_result(principal,UUID(run['run_id']))
    await research.queue_draft(principal.owner_id,UUID(run['run_id']),previous['packet'])
    async with research.database.connection() as conn:
        await conn.execute('UPDATE chatgpt_connection SET revoked_at=now() WHERE id=%s',(principal.connection_id,))
    await ResearchWorker(research).process_one(UUID(run['run_id']))
    result=await research.view_for_owner(principal.owner_id,UUID(run['run_id']))
    assert result['status']=='failed'
    assert result['error_code']=='AUTH_REQUIRED'
