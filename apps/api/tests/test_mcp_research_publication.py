import asyncio
import copy
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest

from card_platform.mcp.media import AttachmentDownloader
from card_platform.mcp.schemas import McpFailure
from test_codex_sales_research import research as synthetic_research
from test_mcp_capture_files import AttachmentStream
from test_mcp_captures import photo
from test_mcp_mutations import revision
from test_mcp_research import research_case as shared_research_case, mutation_case as shared_mutation_case, start

research_case = shared_research_case
mutation_case = shared_mutation_case
pytestmark = pytest.mark.asyncio


async def drafted(case, change=None):
    from card_platform.mcp.research import ResearchWorker
    research, _, principal, _ = case
    run = await start(case)
    await ResearchWorker(research,transport=httpx.MockTransport(lambda r:httpx.Response(403,text='blocked'))).process_one(UUID(run['run_id']))
    original = await research.get_result(principal,UUID(run['run_id']))
    packet = copy.deepcopy(original['packet'])
    sales = synthetic_research()['sales']
    for i,sale in enumerate(sales):
        sale['identity'] = dict(packet['identity'])
        sale['sold_at'] = datetime.now(timezone.utc).isoformat()
        sale['card_images'] = [{'role':'card_front','url':f'https://8.8.8.8/{i}'}]
        if change:
            sale.update(copy.deepcopy(change))
    packet['sales'] = sales
    research.transport = httpx.MockTransport(lambda r:httpx.Response(200,text='Synthetic item auction final amount and identity for owner review'))
    research.downloader = AttachmentDownloader(transport=httpx.MockTransport(
        lambda r:httpx.Response(200,stream=AttachmentStream(photo(['red','green','blue'][int(r.url.path[1:])])))))
    return await research.save_draft(principal.owner_id,UUID(run['run_id']),packet)


async def test_reviewed_sufficient_captured_research_publishes_once_and_keeps_history(research_case):
    from card_platform.mcp.research import PrepareResearchPublicationRequest
    research,tools,principal,item_id = research_case
    from card_platform.schemas import ValuationCreate
    async with tools.database.connection() as conn:
        await tools.repository.add_valuation(conn,item_id,principal.owner_id,ValuationCreate(currency='CNY',low_value=120,fair_value=123,high_value=130))
    draft = await drafted(research_case)
    assert draft['fair_value'] is None
    request = PrepareResearchPublicationRequest(run_id=draft['run_id'],expected_revision=await revision(tools,principal,item_id),idempotency_key=uuid4())
    with pytest.raises(McpFailure):
        await research.prepare_research_publication(principal,request)
    reviewed = await research.approve_review(principal.owner_id,UUID(draft['run_id']),draft['evidence_digest'],uuid4())
    assert reviewed['status']=='completed'
    assert reviewed['fair_value']==39
    op = await research.prepare_research_publication(principal,request)
    await tools.operations.approve(principal.owner_id,uuid4(),op['operation_id'],op['payload_hash'])
    results = await asyncio.gather(*(tools.operations.execute(principal,op['operation_id']) for _ in range(3)))
    assert len({r['readback']['valuation_id'] for r in results})==1
    async with tools.database.connection() as conn:
        rows = await (await conn.execute('SELECT fair_value,model_version FROM valuation_snapshot WHERE collection_item_id=%s',(item_id,))).fetchall()
        assert len(rows)==2
        assert {row['fair_value'] for row in rows}=={Decimal('39'),Decimal('123')}
        assert next(row for row in rows if row['fair_value']==39)['model_version']=='chatgpt-captured-sales-v1'
    assert (await research.prepare_research_publication(principal,request))['operation_id']==op['operation_id']


@pytest.mark.parametrize('change',[
    {'actual_price_known':False,'price_basis':'best_offer_unknown'}, {'sale_status':'active'}, {'lot':True},
    {'images_reviewed':False},{'condition':{'target':'raw','candidate':'damaged','comparable':False}},
    {'sold_at':'2020-01-01T00:00:00Z'}, {'identity':{'product_name':'wrong edition'}}, {'duplicate_check':'owned_copy_history'},
])
async def test_invalid_candidates_are_rejected_after_real_capture_not_promoted_by_review(research_case,change):
    research,_,principal,_=research_case
    draft=await drafted(research_case,change)
    result=await research.approve_review(principal.owner_id,UUID(draft['run_id']),draft['evidence_digest'],uuid4())
    assert result['fair_value'] is None
    assert result['status']=='insufficient_evidence'


async def test_identity_change_and_capture_tampering_invalidate_publication(research_case):
    from card_platform.mcp.research import PrepareResearchPublicationRequest
    research,tools,principal,item_id=research_case
    draft=await drafted(research_case)
    await research.approve_review(principal.owner_id,UUID(draft['run_id']),draft['evidence_digest'],uuid4())
    op=await research.prepare_research_publication(principal,PrepareResearchPublicationRequest(run_id=draft['run_id'],expected_revision=await revision(tools,principal,item_id),idempotency_key=uuid4()))
    await tools.operations.approve(principal.owner_id,uuid4(),op['operation_id'],op['payload_hash'])
    async with tools.database.connection() as conn:
        await conn.execute('UPDATE collection_item SET grade_value=9 WHERE id=%s',(item_id,))
    with pytest.raises(McpFailure):
        await tools.operations.execute(principal,op['operation_id'])
    async with tools.database.connection() as conn:
        assert (await (await conn.execute('SELECT count(*) AS n FROM valuation_snapshot WHERE collection_item_id=%s',(item_id,))).fetchone())['n']==0
        row=await research._owned(conn,principal.owner_id,UUID(draft['run_id']))
    name=next(iter(row['capture_manifest']))
    (research.directory(row)/name).write_text('changed')
    with pytest.raises(McpFailure):
        await research.get_result(principal,UUID(draft['run_id']))


async def test_shared_prepare_validate_cli_uses_same_captured_packet(research_case):
    import json
    from pathlib import Path
    import sys
    research,tools,principal,_=research_case
    draft=await drafted(research_case)
    async with tools.database.connection() as conn:
        row=await research._owned(conn,principal.owner_id,UUID(draft['run_id']))
    path=research.directory(row)/'research.json'
    path.write_text(json.dumps(draft['packet']))
    process=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).resolve().parents[3]/'scripts/codex_sales_research.py'),
        'validate',str(path),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    output,_=await process.communicate()
    assert process.returncode==0
    assert json.loads(output)['fair_value']==39
