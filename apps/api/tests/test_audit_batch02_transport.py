"""Sanitized response shape captured from the public CardHobby interface."""
import httpx
import pytest
from card_platform.ingestion.cardhobby import CardHobbyProvider


@pytest.mark.asyncio
@pytest.mark.parametrize('counts', [(None,None,None),(0,0,1)])
async def test_unbound_shell_is_not_a_successful_empty_search(counts):
    total,count,page=counts
    payload={'result':1,'data':{'Keyword':None,'StatusType':0,'Total':total,
        'TotalCount':count,'PageIndex':page,'PagedMarketItemList':[]}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request:httpx.Response(200,json=payload))) as client:
        provider=CardHobbyProvider(None,'unused',min_interval=0)
        if total is None:
            with pytest.raises(ValueError,match='unbound_search_response'):
                await provider._get_search_payload(client,'Kobe',1)
        else:
            assert await provider._get_search_payload(client,'Kobe',1) == payload


@pytest.mark.asyncio
async def test_null_keyword_and_status_are_valid_when_real_results_exist():
    # Current public API uses null Keyword and StatusType=0 even for valid
    # searches. Reject only the completely uninitialized pagination shell.
    payload={'result':1,'data':{'Keyword':None,'StatusType':0,'Total':1,
        'TotalCount':1,'PageIndex':1,'PagedMarketItemList':[{'ID':'123'}]}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request:httpx.Response(200,json=payload))) as client:
        assert await CardHobbyProvider(None,'unused',min_interval=0)._get_search_payload(client,'Kobe',1)==payload
