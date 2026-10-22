"""Invented purchase history: completeness, resume and private evidence."""
from urllib.parse import urlencode, parse_qs, urlsplit

import httpx
import pytest

from card_platform.cardhobby_account import CardHobbySession
from test_cardhobby_account import LIST, ORDER


@pytest.mark.asyncio
async def test_history_reads_one_page_and_details_without_persisting_contacts():
    assert hasattr(CardHobbySession, 'read_history_page'), 'history must checkpoint by page'
    queries = dict(status=0, code='', idcardName='', searchTxt='', Order_Tag='',
                   beginDate='2000-01-01 00:00:00', endDate='2026-12-31 23:59:59')
    cursor = 'https://cardhobby.com.cn/order/myorders?' + urlencode({**queries, 'page': 2})
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path.lower().startswith('/order/detail/'):
            return httpx.Response(200, text='<p>PRIVATE ADDRESS</p><li>实付总额：￥30.10</li>')
        return httpx.Response(200, text=LIST+ORDER+f'<a href="{cursor}">下一页</a>')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await CardHobbySession(client).read_history_page()
    assert result['next_cursor'] == cursor
    assert result['complete'] is False
    assert result['orders'][0]['paid_total'] == '30.10'
    assert len(seen) == 2 and 'PRIVATE' not in str(result)
    assert seen[0].url.params['searchTxt'] == ''
    assert seen[0].url.params['beginDate'].startswith('2000-01-01')


@pytest.mark.asyncio
@pytest.mark.parametrize('cursor', [
    'https://evil.test/order/myorders?page=2',
    'https://cardhobby.com.cn/order/myorders?searchTxt=LeBron&page=2',
    'https://cardhobby.com.cn/order/myorders?status=1&page=2',
])
async def test_history_rejects_cursor_that_changes_coverage(cursor):
    assert hasattr(CardHobbySession, 'read_history_page')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail('unsafe GET'))) as client:
        with pytest.raises(ValueError):
            await CardHobbySession(client).read_history_page(cursor)


@pytest.mark.asyncio
async def test_history_detail_failure_does_not_report_complete_page():
    assert hasattr(CardHobbySession, 'read_history_page')
    def handle(request):
        return httpx.Response(200, text=LIST+ORDER) if request.url.path.lower() == '/order/myorders' else httpx.Response(200, text=LIST)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match='实付'):
            await CardHobbySession(client).read_history_page()


def test_real_pagination_omits_empty_filters_but_keeps_full_date_scope():
    sparse = 'https://cardhobby.com.cn/order/myorders?'+urlencode(dict(status='0',
        beginDate='2000-01-01 00:00:00',endDate='2026-12-31 23:59:59',pageindex='2'))
    value = CardHobbySession.history_url(sparse)
    query = parse_qs(urlsplit(value).query, keep_blank_values=True)
    assert query['searchTxt'] == [''] and query['code'] == ['']
    assert query['pageindex'] == ['2']
