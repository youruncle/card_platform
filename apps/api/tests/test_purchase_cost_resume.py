"""Successful model observations survive transaction cancellation on disk."""
import hashlib
from uuid import uuid4

import httpx
import pytest

from card_platform.config import Settings
from card_platform.purchase_cost_archive import PurchaseArchive
from card_platform.purchase_cost_images import PurchaseImageObserver, observation_binding, digest
from test_purchase_cost_archive import picture, line


@pytest.mark.asyncio
async def test_outer_timeout_records_started_attempt_and_preserves_cancellation(tmp_path):
    import asyncio
    observer=PurchaseImageObserver(Settings(vision_api_key='fixture',upload_dir=str(tmp_path/'uploads')))
    observer.archive=PurchaseArchive(tmp_path/'archive',uuid4())
    observer.remaining=2
    async def slow(*args):
        observer.remaining-=1
        await asyncio.sleep(10)
    observer._observe_current=slow
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(.01):
            await observer.observe({},line(),'a'*64)
    assert observer.attempt_count({},line(),'a'*64)==1


@pytest.mark.asyncio
async def test_same_pair_reuses_download_and_model_but_changed_target_does_not(monkeypatch, tmp_path):
    data = picture()
    front = tmp_path/'front.png'
    front.write_bytes(data)
    observer = PurchaseImageObserver(Settings(vision_api_key='fixture', upload_dir=str(tmp_path/'uploads')))
    observer.archive = PurchaseArchive(tmp_path/'archive', uuid4())
    observer.verifier._target_path = lambda url: front if url else None
    calls, gets = [], []
    gallery = {'image_urls': line()['image_urls'], 'source_url': line()['item_url']}

    async def fetch(client, item):
        gets.append(item['item_id'])
        return gallery

    async def observe(card, item, order_hash, targets, images, group):
        calls.append(card)
        binding = observation_binding(card, item['item_id'], order_hash,
            [hashlib.sha256(b).hexdigest() for b in targets], [hashlib.sha256(b).hexdigest() for b in images])
        binding['gallery_sha256'] = digest(group)
        return {'source':'purchase_multi_image','verdict':'uncertain','cost_authorized':False,
                'evidence_binding':binding,'reasons':['fixed_finish_evidence_required']}

    observer.gallery = fetch
    observer.observe_bytes = observe
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(lambda _: httpx.Response(200,content=data)), **kwargs))
    card = {'image_front_url':'/uploads/front.png','card_number':'17'}
    first = await observer.observe(card, line(), 'a'*64)
    assert await observer.observe(card, line(), 'a'*64) == first
    assert len(calls) == 1 and len(gets) == 1
    await observer.observe({**card, 'card_number':'18'}, line(), 'a'*64)
    assert len(calls) == 2
    await observer.observe(card, line(), 'b'*64)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_failed_download_consumes_step_budget_instead_of_fetching_every_candidate(tmp_path):
    from card_platform.purchase_cost_images import ObservationPending
    data = picture()
    front = tmp_path/'front.png'; front.write_bytes(data)
    observer = PurchaseImageObserver(Settings(vision_api_key='fixture', upload_dir=str(tmp_path/'uploads')))
    observer.archive = PurchaseArchive(tmp_path/'archive', uuid4())
    observer.remaining = 1
    observer.verifier._target_path = lambda url: front if url else None
    calls = []
    async def unavailable(client, item):
        calls.append(item)
        raise ValueError('remote gallery unavailable')
    observer.gallery = unavailable
    with pytest.raises(ValueError):
        await observer.observe({'image_front_url':'/uploads/front.png'}, line(), 'a'*64)
    with pytest.raises(ObservationPending):
        await observer.observe({'image_front_url':'/uploads/front.png'}, line(), 'a'*64)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_failed_head_candidates_do_not_starve_untried_tail(monkeypatch, tmp_path):
    from card_platform import purchase_cost_service as module
    folder = tmp_path/'uploads'; folder.mkdir()
    (folder/'front.png').write_bytes(picture())
    settings = Settings(vision_api_key='fixture', upload_dir=str(folder))
    monkeypatch.setattr(module,'get_settings',lambda:settings)
    gets = []
    async def fetch(self, client, item):
        gets.append(item['item_id'])
        if item['item_id'] in {'1000','1001'}:
            raise ValueError('remote gallery unavailable')
        return {'image_urls':item['image_urls'],'source_url':item['item_url']}
    async def observe(self, card, item, order_hash, targets, images, group):
        return {'source':'purchase_multi_image','verdict':'uncertain','reasons':['fixed_finish_evidence_required']}
    monkeypatch.setattr(PurchaseImageObserver,'gallery',fetch)
    monkeypatch.setattr(PurchaseImageObserver,'observe_bytes',observe)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(
        transport=httpx.MockTransport(lambda _:httpx.Response(200,content=picture())),**kwargs))
    class Conn:
        async def execute(self,*args):
            return None
    costs = module.PurchaseCostService()
    async def incomplete(*args):
        pass
    monkeypatch.setattr(costs,'incomplete_field',incomplete)
    rows = [dict(unit_id=str(uuid4()),order_id=str(2000+i),item_id=str(1000+i),evidence_hash='a'*64,
                 title='Fixture', item_url=f'https://cardhobby.com.cn/market/item/{1000+i}',
                 image_urls=['https://images.cardhobby.com/a.png'],automatic=False,recalled=True,
                 conflicts=[],missing=[],reasons=['产品一致']) for i in range(4)]
    owner,item = uuid4(),uuid4()
    card = {'image_front_url':'/uploads/front.png'}
    await costs.observe_purchase_field(Conn(),owner,item,card,rows,[])
    await costs.observe_purchase_field(Conn(),owner,item,card,rows,[])
    assert gets == ['1000','1001','1002','1003']
