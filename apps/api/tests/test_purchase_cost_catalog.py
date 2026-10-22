"""Product OCR must be independent, reusable and never cost authority."""
import importlib.util
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_purchase_cost_archive import archive, picture, line


def test_unknown_absent_grade_does_not_discard_readable_product_text():
    from card_platform.purchase_cost_catalog import validate_product
    result = validate_product(dict(photos=[dict(index=0,role='front')],single_card=True,fields=[
        dict(kind='player',value='VJ Edgecombe',text='VJ EDGECOMBE',photo=0,origin='printed'),
        dict(kind='grade',value='unknown',text='',photo=0,origin='unknown'),
        dict(kind='grade_cert',value='unknown',text='',photo=0,origin='unknown')]),1)
    assert [f.kind for f in result.fields] == ['player']


def test_auxiliary_copyright_text_is_not_an_identity_schema_failure():
    from card_platform.purchase_cost_catalog import validate_product
    value=dict(photos=[dict(index=0,role='back')],single_card=True,fields=[
        dict(kind='card_number',value='153',text='No 153',photo=0,origin='printed'),
        dict(kind='copyright',value='2016 Panini',text='2016 Panini',photo=0,origin='printed')])
    result=validate_product(value,1)
    assert [f.kind for f in result.fields]==['card_number']


@pytest.mark.asyncio
async def test_product_ocr_reuses_only_current_images_and_preserves_unknowns(monkeypatch, tmp_path):
    assert importlib.util.find_spec('card_platform.purchase_cost_catalog')
    from card_platform.purchase_cost_catalog import extract_product
    from card_platform.purchase_cost_images import PurchaseImageObserver
    from card_platform.config import Settings
    observer = PurchaseImageObserver(Settings(vision_api_key='fixture', upload_dir=str(tmp_path/'uploads')))
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"photos":[{"index":0,"role":"front"}],"fields":[{"kind":"player","value":"Example Player","text":"EXAMPLE PLAYER","photo":0,"origin":"printed"}],"single_card":true}'))])
    monkeypatch.setattr(observer.verifier.client.chat.completions, 'create', create)
    a = archive(tmp_path/'private', uuid4())
    gallery = {'image_urls': line()['image_urls'], 'source_url': line()['item_url']}
    first = await extract_product(a, observer, line(), gallery, [picture()])
    second = await extract_product(a, observer, line(), gallery, [picture()])
    assert len(calls) == 1
    assert first == second and first['cost_authorized'] is False
    assert first['observations']['fields'][0]['text'] == 'EXAMPLE PLAYER'
    assert not any(f['kind'] == 'serial' for f in first['observations']['fields'])
    assert 'TARGET' not in str(calls[0]['messages'])
    assert 'Example Player' not in str(calls[0]['messages'])


@pytest.mark.asyncio
async def test_product_ocr_rejects_fields_on_unclassified_photo(monkeypatch, tmp_path):
    assert importlib.util.find_spec('card_platform.purchase_cost_catalog')
    from card_platform.purchase_cost_catalog import validate_product
    with pytest.raises(ValueError):
        validate_product({'photos':[{'index':0,'role':'front'}], 'fields':[
            {'kind':'serial','value':'3/25','text':'3/25','photo':1,'origin':'printed'}], 'single_card':True}, 1)


def test_product_words_can_recall_missing_seller_name_but_never_authorize_cost():
    from card_platform import purchase_cost_catalog
    from card_platform.knowledge import KnowledgeResolver
    assert hasattr(purchase_cost_catalog, 'score_from_product')
    card = {'player_name':'Example Player', 'product_name':'Chrome', 'brand_name':'Topps', 'season_start':2025}
    product = {'observations':{'photos':[{'index':0,'role':'front'}], 'single_card':True, 'fields':[
        {'kind':kind,'value':value,'text':value,'photo':0,'origin':'printed'}
        for kind,value in [('player','Example Player'),('product','Chrome'),('brand','Topps')]]}}
    result = purchase_cost_catalog.score_from_product(card,
        {**line(),'title':'2025 Topps Chrome 卡片'}, KnowledgeResolver(), product)
    assert result['recalled'] is True
    assert result['automatic'] is False
    assert '商品图文字召回' in result['reasons']


@pytest.mark.asyncio
async def test_bad_product_read_retains_raw_private_evidence(monkeypatch, tmp_path):
    import json
    from card_platform.purchase_cost_catalog import extract_product
    from card_platform.purchase_cost_images import PurchaseImageObserver
    from card_platform.config import Settings
    observer = PurchaseImageObserver(Settings(vision_api_key='fixture', upload_dir=str(tmp_path/'uploads')))
    raw = '{"photos":[{"index":0,"role":"front"}],"fields":[{"kind":"grade_cert","value":"PRIZM","text":"PRIZM","photo":0,"origin":"printed"}],"single_card":true}'
    async def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw))])
    monkeypatch.setattr(observer.verifier.client.chat.completions, 'create', create)
    a = archive(tmp_path/'private')
    with pytest.raises(ValueError):
        await extract_product(a,observer,line(),{},[picture()])
    records = [json.loads(p.read_text())['value'] for p in (a.root/'runs').glob('*')]
    assert any(r.get('raw_response') == raw for r in records)
    assert all(r.get('cost_authorized') is False for r in records)


@pytest.mark.asyncio
@pytest.mark.parametrize('include_copyright', [False, True])
@pytest.mark.parametrize('invalid_field', [
    dict(kind='autograph', value='God Bless J', text='God Bless J', photo=0, origin='autograph'),
    dict(kind='card_number', value='052/150', text='052/150', photo=0, origin='printed'),
])
async def test_product_rereads_real_schema_and_fraction_failures_once(monkeypatch, tmp_path, invalid_field, include_copyright):
    import json
    from card_platform.purchase_cost_catalog import extract_product
    from card_platform.purchase_cost_images import PurchaseImageObserver
    from card_platform.config import Settings
    observer = PurchaseImageObserver(Settings(vision_api_key='fixture', upload_dir=str(tmp_path/'uploads')))
    valid = dict(photos=[dict(index=0, role='front')], single_card=True, fields=[
        dict(kind='player', value='Example Player', text='EXAMPLE PLAYER', photo=0, origin='printed'),
        dict(kind='serial', value='052/150', text='052/150', photo=0, origin='printed')])
    raw = json.dumps({**valid, 'fields':[*valid['fields'], invalid_field]})
    auxiliary = [dict(kind='copyright', value='2025', text='2025', photo=0, origin='printed')] if include_copyright else []
    corrected = json.dumps({**valid, 'fields':[*valid['fields'], *auxiliary]})
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw if len(calls)==1 else corrected))])
    monkeypatch.setattr(observer.verifier.client.chat.completions, 'create', create)
    a = archive(tmp_path/'private')
    result = await extract_product(a, observer, line(), {}, [picture()])
    assert result['observations']['fields'] == valid['fields']
    assert result['cost_authorized'] is False
    assert len(calls) == 2
    records = [json.loads(p.read_text())['value'] for p in (a.root/'runs').glob('*')]
    assert {r['raw_response'] for r in records} >= {raw, corrected}
    assert all(r['cost_authorized'] is False for r in records)


@pytest.mark.asyncio
async def test_product_repair_cannot_erase_valid_serial_or_repeat_indefinitely(monkeypatch, tmp_path):
    import json
    from card_platform.purchase_cost_catalog import extract_product
    from card_platform.purchase_cost_images import PurchaseImageObserver
    from card_platform.config import Settings
    observer = PurchaseImageObserver(Settings(vision_api_key='fixture', upload_dir=str(tmp_path/'uploads')))
    raw = json.dumps(dict(photos=[dict(index=0,role='front')], single_card=True, fields=[
        dict(kind='serial',value='052/150',text='052/150',photo=0,origin='printed'),
        dict(kind='card_number',value='052/150',text='052/150',photo=0,origin='printed')]))
    erased = json.dumps(dict(photos=[dict(index=0,role='front')], single_card=True, fields=[]))
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw if len(calls)==1 else erased))])
    monkeypatch.setattr(observer.verifier.client.chat.completions, 'create', create)
    a = archive(tmp_path/'private')
    with pytest.raises(ValueError):
        await extract_product(a, observer, line(), {}, [picture()])
    assert len(calls) == 2
    assert not list((a.root/'products').glob('*')) if (a.root/'products').exists() else True
