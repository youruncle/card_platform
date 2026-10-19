import importlib.util
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from card_platform.config import Settings


@pytest.mark.asyncio
async def test_product_preparation_overlaps_two_independent_listings_with_bound(monkeypatch,tmp_path):
    import asyncio,copy
    from card_platform import purchase_cost_history as module
    from card_platform.purchase_cost_archive import PurchaseArchive
    from test_purchase_cost_postgres import order
    settings=Settings(upload_dir=str(tmp_path/'uploads'),vision_api_key='fixture')
    owner=uuid4();row=order();second=copy.deepcopy(row['lines'][0])
    second['item_id']='999992';row['lines'].append(second)
    class Account:
        async def archive_orders(self,*args,**kwargs):return [row]
    a=PurchaseArchive.configured(settings,owner)
    a.save_page(dict(account_key='a'*64,orders=[row],complete=True,next_cursor=None),None)
    entered=0;both=asyncio.Event()
    async def images(self,line):return {},[]
    async def extract(*args):
        nonlocal entered
        entered+=1
        if entered==2:both.set()
        await asyncio.wait_for(both.wait(),.2)
        return dict(cost_authorized=False)
    monkeypatch.setattr(module.PurchaseImageObserver,'gallery_bytes',images)
    monkeypatch.setattr(module,'extract_product',extract)
    result=await module.PurchaseHistoryService(settings,Account(),None).prepare(None,owner,limit=2)
    assert result['ready_products']==2 and result['failed']==0


@pytest.mark.asyncio
async def test_sync_restarts_completed_history_when_account_or_freshness_changed(tmp_path):
    from card_platform.purchase_cost_history import PurchaseHistoryService
    from card_platform.purchase_cost_archive import PurchaseArchive
    settings = Settings(upload_dir=str(tmp_path/'uploads'))
    owner = uuid4()
    a = PurchaseArchive.configured(settings, owner)
    a.save_page(dict(account_key='a'*64,orders=[],next_cursor=None,complete=True),None)
    class Account:
        async def archive_orders(self,*args,**kwargs):
            return None
        async def history_page(self,conn,owner,cursor):
            assert cursor is None
            return dict(account_key='b'*64,orders=[],next_cursor=None,complete=True)
    class Costs:
        async def sync(self,*args):
            return {}
    await PurchaseHistoryService(settings,Account(),Costs()).sync_page(None,owner)
    assert a.state()['account_key']=='b'*64


@pytest.mark.asyncio
async def test_sync_restarts_expired_partial_scan_instead_of_renewing_old_pages(tmp_path):
    from card_platform.purchase_cost_history import PurchaseHistoryService
    from card_platform.purchase_cost_archive import PurchaseArchive

    settings = Settings(upload_dir=str(tmp_path/'uploads'))
    owner = uuid4()
    a = PurchaseArchive.configured(settings, owner)
    a.save_page(dict(account_key='a'*64, orders=[], next_cursor='page2', complete=False), None)
    state = a.state()
    state['captured_at'] = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    a.write('state', '0'*64, state)

    class Account:
        async def history_page(self, conn, owner, cursor):
            assert cursor is None
            return dict(account_key='a'*64, orders=[], next_cursor=None, complete=True)

    class Costs:
        async def sync(self, *args):
            return {}

    result = await PurchaseHistoryService(settings, Account(), Costs()).sync_page(None, owner)
    assert result['complete'] is True
    assert result['pages'] == 1


@pytest.mark.asyncio
async def test_sync_checkpoints_only_complete_pages_and_resumes_after_failure(tmp_path):
    assert importlib.util.find_spec('card_platform.purchase_cost_history')
    from card_platform.purchase_cost_history import PurchaseHistoryService
    from card_platform.purchase_cost_archive import PurchaseArchive
    class Account:
        calls = []
        fail = True
        async def history_page(self, conn, owner, cursor):
            self.calls.append(cursor)
            if cursor == 'page2' and self.fail:
                raise TimeoutError()
            return dict(account_key='a'*64,orders=[],next_cursor='page2' if cursor is None else None,
                        complete=cursor is not None)
    class Costs:
        async def sync(self, conn, owner, orders):
            return {}
    owner = uuid4()
    settings = Settings(upload_dir=str(tmp_path/'uploads'))
    account = Account()
    history = PurchaseHistoryService(settings, account, Costs())
    assert (await history.sync_page(None, owner))['complete'] is False
    with pytest.raises(TimeoutError):
        await history.sync_page(None, owner)
    assert PurchaseArchive.configured(settings, owner).state()['next_cursor'] == 'page2'
    account.fail = False
    assert (await history.sync_page(None, owner))['complete'] is True
    assert account.calls == [None, 'page2', 'page2']


@pytest.mark.asyncio
async def test_empty_browser_capture_resolves_complete_owned_archive_on_server(monkeypatch):
    from card_platform import cardhobby_account_api
    from card_platform.purchase_cost_service import PurchaseCostService
    from test_purchase_cost_postgres import order
    capture = order()
    class Account:
        async def archive_orders(self, conn, owner):
            return [capture]
    monkeypatch.setattr(cardhobby_account_api, 'account_service', lambda: Account())
    service = PurchaseCostService()
    seen = []
    async def resolve(conn, owner, item, orders, complete, refresh=False, *, trace):
        seen.extend(orders)
        return {'status':'unmatched','candidates':[]}
    monkeypatch.setattr(service, '_resolve_search', resolve)
    await service.resolve_search(None, uuid4(), uuid4(), [], True)
    assert [x.external_order_id for x in seen] == [capture['external_order_id']]


@pytest.mark.asyncio
async def test_preparation_progress_restarts_when_same_order_has_changed_images(monkeypatch, tmp_path):
    from card_platform import purchase_cost_history as module
    from card_platform.purchase_cost_archive import PurchaseArchive
    from test_purchase_cost_postgres import order
    settings = Settings(upload_dir=str(tmp_path/'uploads'), vision_api_key='fixture')
    owner = uuid4()
    row = order(); row['lines'][0]['image_urls'] = ['https://images.cardhobby.com/a.png']
    class Account:
        async def archive_orders(self, *args, **kwargs):
            return [row]
    a = PurchaseArchive.configured(settings, owner)
    a.save_page(dict(account_key='a'*64,orders=[row],complete=True,next_cursor=None),None)
    async def images(self, line):
        return {}, []
    async def extract(*args):
        return {'cost_authorized':False}
    monkeypatch.setattr(module.PurchaseImageObserver,'gallery_bytes',images)
    monkeypatch.setattr(module,'extract_product',extract)
    service = module.PurchaseHistoryService(settings,Account(),None)
    assert (await service.prepare(None,owner))['ready_products'] == 1
    row['lines'][0]['image_urls'] = ['https://images.cardhobby.com/b.png']
    second = await service.prepare(None,owner)
    assert second['attempted'] == 1
    assert second['processed'] == 1 and second['remaining'] == 0


@pytest.mark.asyncio
async def test_expired_gallery_is_reprepared_even_when_orders_are_unchanged(monkeypatch, tmp_path):
    import json, hashlib
    from datetime import datetime,timedelta,timezone
    from card_platform import purchase_cost_history as module
    from card_platform.purchase_cost_archive import PurchaseArchive
    from card_platform.purchase_cost_catalog import CONTRACT
    from test_purchase_cost_archive import picture
    from test_purchase_cost_postgres import order
    settings = Settings(upload_dir=str(tmp_path/'uploads'),vision_api_key='fixture')
    owner = uuid4(); row = order()
    row['lines'][0]['image_urls'] = ['https://images.cardhobby.com/a.png']
    class Account:
        async def archive_orders(self,*args,**kwargs):
            return [row]
    a = PurchaseArchive.configured(settings,owner)
    a.save_page(dict(account_key='a'*64,orders=[row],complete=True,next_cursor=None),None)
    calls = []
    async def images(self,line):
        calls.append(line['item_id'])
        gallery = {'image_urls':line['image_urls'],'source_url':line['item_url']}
        a.save_gallery(line,gallery,[picture()])
        return gallery,[picture()]
    async def extract(archive,observer,line,*args):
        return dict(binding=dict(contract=CONTRACT,item_id=line['item_id'],item_url=line['item_url'],
                                 image_hashes=[hashlib.sha256(picture()).hexdigest()]),
                    observations=dict(photos=[dict(index=0,role='front')],fields=[],single_card=True),
                    cost_authorized=False)
    monkeypatch.setattr(module.PurchaseImageObserver,'gallery_bytes',images)
    monkeypatch.setattr(module,'extract_product',extract)
    service = module.PurchaseHistoryService(settings,Account(),None)
    await service.prepare(None,owner)
    path = next((a.root/'galleries').iterdir());body=json.loads(path.read_text())
    body['saved_at']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    path.write_text(json.dumps(body))
    result=await service.prepare(None,owner)
    assert result['attempted']==1 and len(calls)==2
