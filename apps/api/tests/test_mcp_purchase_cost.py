from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from card_platform.purchase_cost_service import PurchaseCostService
from test_mcp_mutations import mutation_case as shared_mutation_case
from test_purchase_cost import capture
from test_mcp_mutations import revision

mutation_case = shared_mutation_case
pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('preview_only', [True, False])
async def test_unique_purchase_preview_never_confirms_but_original_path_does(mutation_case, monkeypatch, preview_only):
    tools, principal, ids = mutation_case
    service = PurchaseCostService()
    candidate = {'automatic': True, 'unit_id': str(uuid4()), 'evidence_hash': 'a'*64,
                 'order_id': '900001', 'card_price': '100', 'shipping': '5', 'total_cost': '105'}
    monkeypatch.setattr(service, 'search_plan', AsyncMock(return_value={'status':'ready','keywords':['Curry']}))
    monkeypatch.setattr(service, 'candidates', AsyncMock(return_value={'candidates':[candidate],'rejected':[]}))
    monkeypatch.setattr(service, 'confirm', AsyncMock(return_value={'status':'recorded'}))
    monkeypatch.setattr(service, 'sync', AsyncMock())
    monkeypatch.setattr(service, 'prepare_bundle_inference', AsyncMock())
    monkeypatch.setattr(service, 'prepare_visual_inference', AsyncMock(return_value={}))
    async with tools.database.connection() as conn:
        result = await service._resolve_search(conn, principal.owner_id, ids[0], [capture()], True,
            refresh=True, trace={'recall':{}}, preview_only=preview_only)
    if preview_only:
        assert result['status'] == 'matched'
        assert result['candidates'] == [candidate]
        service.confirm.assert_not_awaited()
    else:
        assert result['status'] == 'recorded'
        service.confirm.assert_awaited_once()


async def test_lookup_preview_does_not_schedule_automatic_cost_continuation(mutation_case, monkeypatch):
    tools, principal, ids = mutation_case
    service = PurchaseCostService()
    monkeypatch.setattr(service, '_resolve_search', AsyncMock(return_value={
        'status':'unmatched','verification_issues':[{'code':'multi_photo_pending'}]}))
    async with tools.database.connection() as conn:
        await conn.execute('DELETE FROM automatic_purchase_cost WHERE item_id=%s', (ids[0],))
        result = await service.resolve_search(conn, principal.owner_id, ids[0], [], False, preview_only=True)
        assert result['trace']['commit']['written'] is False
        assert not await (await conn.execute('SELECT 1 FROM automatic_purchase_cost WHERE item_id=%s', (ids[0],))).fetchone()


async def test_durable_lookup_idempotency_ownership_and_readonly_worker(mutation_case):
    from card_platform.mcp.purchase_cost import PurchaseLookupTools, PurchaseLookupWorker, StartPurchaseLookup
    tools, principal, ids = mutation_case
    account = AsyncMock()
    account.search.return_value = {'orders':[], 'complete':False}
    service = PurchaseCostService()
    service.search_plan = AsyncMock(return_value={'status':'ready','keywords':['Curry']})
    service.resolve_search = AsyncMock(return_value={'status':'incomplete','reason':'partial','candidates':[]})
    lookup = PurchaseLookupTools(tools.database, tools.operations, service=service, account_factory=lambda:account)
    request = StartPurchaseLookup(item_id=ids[0],expected_revision=await revision(tools,principal,ids[0]),idempotency_key=uuid4())
    a = await lookup.start(principal, request)
    assert (await lookup.start(principal, request))['lookup_id'] == a['lookup_id']
    await PurchaseLookupWorker(lookup).process_one(a['lookup_id'])
    result = await lookup.get(principal, a['lookup_id'])
    assert result['status']=='completed'
    assert result['result']['status']=='incomplete'
    assert service.resolve_search.await_args.kwargs['preview_only'] is True
    assert service.resolve_search.await_args.kwargs['refresh'] is True
    from card_platform.mcp.schemas import McpFailure
    with pytest.raises(McpFailure):
        await lookup.get(principal.model_copy(update={'owner_id':uuid4()}),a['lookup_id'])
    with pytest.raises(McpFailure):
        await lookup.start(principal, request.model_copy(update={'item_id':ids[1]}))
    async with tools.database.connection() as conn:
        await conn.execute('UPDATE chatgpt_connection SET revoked_at=now() WHERE id=%s',(principal.connection_id,))
    with pytest.raises(McpFailure):
        await lookup.get(principal,a['lookup_id'])


async def test_keyword_lookup_without_inventory_searches_exact_supplied_keywords_and_paginates(mutation_case):
    from card_platform.mcp.purchase_cost import PurchaseLookupTools, PurchaseLookupWorker, StartPurchaseLookup
    tools, principal, _ = mutation_case
    account=AsyncMock()
    account.search.return_value={'orders':[capture().model_dump(mode='json')],'complete':True}
    service=PurchaseCostService()
    service.resolve_search=AsyncMock(side_effect=AssertionError('keyword path must not resolve or confirm inventory'))
    lookup=PurchaseLookupTools(tools.database,tools.operations,service=service,account_factory=lambda:account)
    started=await lookup.start(principal,StartPurchaseLookup(keywords=['Test Player'],idempotency_key=uuid4()))
    await PurchaseLookupWorker(lookup).process_one(started['lookup_id'])
    result=await lookup.get(principal,started['lookup_id'],page=1,page_size=2)
    assert result['item_id'] is None and result['ledger_modified'] is False
    assert result['result']['total']==3 and result['result']['next_page']==2
    assert [r['shipping'] for r in result['result']['candidates']]==['0.04','0.03']
    assert result['result']['candidates'][0]['total_cost']=='10.04'
    assert account.search.await_args.args[2]==['Test Player']
    assert account.search.await_args.kwargs['prefer_live'] is True
    service.resolve_search.assert_not_awaited()


async def test_lookup_revoked_before_worker_never_searches_and_sensitive_error_is_not_returned(mutation_case):
    from card_platform.mcp.purchase_cost import PurchaseLookupTools, PurchaseLookupWorker, StartPurchaseLookup
    from card_platform.cardhobby_account import AccountError
    tools,principal,_=mutation_case
    account=AsyncMock()
    account.search.side_effect=AccountError('VERIFICATION_REQUIRED','secret-password-cookie')
    lookup=PurchaseLookupTools(tools.database,tools.operations,account_factory=lambda:account)
    a=await lookup.start(principal,StartPurchaseLookup(keywords=['Curry'],idempotency_key=uuid4()))
    await PurchaseLookupWorker(lookup).process_one(a['lookup_id'])
    view=await lookup.get(principal,a['lookup_id'])
    assert view['status']=='account_required' and view['error_code']=='VERIFICATION_REQUIRED'
    assert 'secret-password-cookie' not in str(view)
    b=await lookup.start(principal,StartPurchaseLookup(keywords=['Hoops'],idempotency_key=uuid4()))
    account.search.reset_mock()
    async with tools.database.connection() as conn:
        await conn.execute('UPDATE chatgpt_connection SET revoked_at=now() WHERE id=%s',(principal.connection_id,))
    await PurchaseLookupWorker(lookup).process_one(b['lookup_id'])
    account.search.assert_not_awaited()
    async with tools.database.connection() as conn:
        row=await (await conn.execute('SELECT status FROM chatgpt_purchase_lookup WHERE id=%s',(b['lookup_id'],))).fetchone()
        assert row['status']=='cancelled'


async def test_lookup_changed_target_invalidates_result_and_same_key_replay(mutation_case):
    from card_platform.mcp.purchase_cost import PurchaseLookupTools, StartPurchaseLookup
    from card_platform.mcp.schemas import McpFailure
    tools,principal,ids=mutation_case
    lookup=PurchaseLookupTools(tools.database,tools.operations)
    req=StartPurchaseLookup(item_id=ids[0],expected_revision=await revision(tools,principal,ids[0]),idempotency_key=uuid4())
    a=await lookup.start(principal,req)
    async with tools.database.connection() as conn:
        await conn.execute("UPDATE collection_item SET notes='changed' WHERE id=%s",(ids[0],))
    with pytest.raises(McpFailure):
        await lookup.get(principal,a['lookup_id'])
    with pytest.raises(McpFailure):
        await lookup.start(principal,req)


async def test_live_keyword_mode_bypasses_archive_but_existing_callers_keep_archive_shortcut():
    from card_platform.cardhobby_account import CardHobbyAccountService
    account=object.__new__(CardHobbyAccountService)
    account.archive_orders=AsyncMock(return_value=[])
    account._read=AsyncMock(return_value={'orders':[], 'complete':True})
    owner=uuid4()
    await account.search(None,owner,['Curry'],prefer_live=True)
    account.archive_orders.assert_not_awaited()
    account._read.assert_awaited_once()
    account._read.reset_mock()
    await account.search(None,owner,['Curry'])
    account.archive_orders.assert_awaited_once()
    account._read.assert_not_awaited()


async def test_worker_concurrent_claims_and_revocation_during_search(mutation_case):
    import asyncio
    from card_platform.mcp.purchase_cost import PurchaseLookupTools, PurchaseLookupWorker, StartPurchaseLookup
    tools,principal,_=mutation_case
    account=AsyncMock()
    async def search(*args,**kwargs):
        async with tools.database.connection() as conn:
            await conn.execute('UPDATE chatgpt_connection SET revoked_at=now() WHERE id=%s',(principal.connection_id,))
        return {'orders':[capture().model_dump(mode='json')],'complete':True}
    account.search.side_effect=search
    lookup=PurchaseLookupTools(tools.database,tools.operations,account_factory=lambda:account)
    a=await lookup.start(principal,StartPurchaseLookup(keywords=['Test Player'],idempotency_key=uuid4()))
    claimed=await asyncio.gather(*(PurchaseLookupWorker(lookup).process_one(a['lookup_id']) for _ in range(3)))
    assert sum(claimed)==1
    account.search.assert_awaited_once()
    async with tools.database.connection() as conn:
        row=await (await conn.execute('SELECT status,result FROM chatgpt_purchase_lookup WHERE id=%s',(a['lookup_id'],))).fetchone()
        assert row=={'status':'cancelled','result':{}}


async def test_expired_lease_stops_after_three_attempts_without_more_search(mutation_case):
    from card_platform.mcp.purchase_cost import PurchaseLookupTools, PurchaseLookupWorker, StartPurchaseLookup
    tools,principal,_=mutation_case
    account=AsyncMock()
    lookup=PurchaseLookupTools(tools.database,tools.operations,account_factory=lambda:account)
    a=await lookup.start(principal,StartPurchaseLookup(keywords=['Test'],idempotency_key=uuid4()))
    async with tools.database.connection() as conn:
        await conn.execute("UPDATE chatgpt_purchase_lookup SET status='running',attempts=3,lease_until=now()-interval '1 minute' WHERE id=%s",(a['lookup_id'],))
    await PurchaseLookupWorker(lookup).process_one(a['lookup_id'])
    account.search.assert_not_awaited()
    assert (await lookup.get(principal,a['lookup_id']))['error_code']=='RETRY_EXHAUSTED'


async def test_punctuation_cannot_become_an_unfiltered_order_query():
    from pydantic import ValidationError
    from card_platform.mcp.purchase_cost import StartPurchaseLookup
    with pytest.raises(ValidationError):
        StartPurchaseLookup(keywords=['!!!'],idempotency_key=uuid4())


async def test_marketplace_network_error_does_not_ask_user_to_log_in(mutation_case):
    from card_platform.cardhobby_account import AccountError
    from card_platform.mcp.purchase_cost import PurchaseLookupTools, PurchaseLookupWorker, StartPurchaseLookup
    tools,principal,_=mutation_case
    account=AsyncMock()
    account.search.side_effect=AccountError('NETWORK_ERROR','private details')
    lookup=PurchaseLookupTools(tools.database,tools.operations,account_factory=lambda:account)
    job=await lookup.start(principal,StartPurchaseLookup(keywords=['Curry'],idempotency_key=uuid4()))
    await PurchaseLookupWorker(lookup).process_one(job['lookup_id'])
    result=await lookup.get(principal,job['lookup_id'])
    assert result['status']=='failed' and result['error_code']=='NETWORK_ERROR'
