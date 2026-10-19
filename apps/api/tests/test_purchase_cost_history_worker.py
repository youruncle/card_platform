from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from card_platform.auto_purchase_cost import lookup


@pytest.mark.asyncio
async def test_pending_candidate_field_requeues_without_spending_failure_attempt():
    service = AsyncMock()
    service.search_plan.return_value = {'status':'ready','keywords':['Example']}
    service.resolve_search.return_value = {'status':'unmatched', 'verification_issues':[
        {'code':'multi_photo_pending'}], 'reason':'Candidate field not finished',
        'trace':{'verification':{'new_observations':1}}}
    account = AsyncMock()
    account.search.return_value = {'orders':[], 'complete':True}
    result = await lookup(None, service, {'owner_id':uuid4(),'item_id':uuid4()}, lambda:account)
    assert result.get('_retry') is True
    assert result.get('_progress') is True
