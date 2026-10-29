"""Auction copy must describe only known facts; media remains owner private."""
from io import BytesIO
from uuid import uuid4

from PIL import Image
import pytest


def service():
    from card_platform import auction_drafts
    return auction_drafts


def picture():
    output = BytesIO()
    Image.new('RGB', (60, 90), 'navy').save(output, format='PNG')
    return output.getvalue()


def test_copy_uses_confirmed_identity_and_omits_unknown_promises():
    identity = dict(player_name='Stephen Curry 库里', season_start=2023, season_end=2024,
                    brand_name='Panini', product_name='Prizm', card_number='001',
                    parallel_name=None, is_autograph=None, is_rookie=None,
                    serial_number=None, serial_total=None, grade_company=None)
    generated = service().generate_copy(identity, 2, '', [], 'unavailable')
    assert '2023-24' in generated['title']
    assert '#001' in generated['title']
    assert 'Stephen Curry 库里' in generated['title']
    for unsupported in ('签字', '签名', 'RC', '完美品', '保真', '包邮', '1元起拍', 'PSA'):
        assert unsupported not in generated['title'] + generated['description']
    assert '待' in generated['description']
    assert generated['generation_basis']['examples_status'] == 'unavailable'


def test_explicit_serial_grade_and_condition_without_copying_seller_claims():
    generated = service().generate_copy(
        dict(player_name='测试球员', season_start=2025, brand_name='Topps',
             product_name='Chrome', is_autograph=True, autograph_type='on_card',
             serial_number=3, serial_total=25, grade_company='PSA', grade_value='9'),
        3, '背面左下角有白点',
        [dict(title='包邮 完美品 1元起拍 人见人爱', item_url='https://cardhobby.com.cn/market/item/123')],
        'used',
    )
    assert '3/25' in generated['title'] and 'PSA 9' in generated['title']
    assert '卡签' in generated['title']
    assert '背面左下角有白点' in generated['description']
    assert all(x not in generated['title'] + generated['description'] for x in ('包邮', '完美品', '1元起拍'))
    assert generated['generation_basis']['examples'][0]['item_url'].endswith('/123')


def test_selection_rejects_empty_duplicates_foreign_and_too_many_photos():
    ids = [str(uuid4()) for _ in range(13)]
    photos = [dict(id=id) for id in ids]
    assert service().validate_selection([ids[1], ids[0]], photos[:12]) == [ids[1], ids[0]]
    for selection in ([], [ids[0], ids[0]], [str(uuid4())], ids):
        with pytest.raises(ValueError):
            service().validate_selection(selection, photos)


def test_media_is_immutable_owner_scoped_and_cannot_traverse(tmp_path):
    store = service().AuctionMediaStore(tmp_path)
    owner, draft = uuid4(), uuid4()
    photo = store.save(owner, draft, picture(), 'front', 'redmi')
    assert photo['source'] == 'redmi'
    assert store.resource(owner, draft, photo).read_bytes() == picture()
    assert store.resource(owner, draft, photo).stat().st_mode & 0o077 == 0
    with pytest.raises(FileNotFoundError):
        store.resource(uuid4(), draft, photo)
    with pytest.raises(FileNotFoundError):
        store.resource(owner, draft, {**photo, 'filename': '../../private.env'})
    assert not list(tmp_path.glob('*.png'))
    with pytest.raises(ValueError):
        store.save(owner, draft, b'not an image', 'front', 'upload')


def test_conflict_and_invalid_selected_photo_leave_text_unchanged():
    original = dict(revision=4, title='人工标题', description='人工描述', condition_notes='',
                    selected_photo_ids=[], photos=[dict(id='a')])
    with pytest.raises(service().DraftConflict):
        service().apply_edit(original, 3, dict(title='过期修改'))
    with pytest.raises(ValueError):
        service().apply_edit(original, 4, dict(selected_photo_ids=['foreign']))
    assert original['title'] == '人工标题' and original['revision'] == 4
    updated = service().apply_edit(original, 4, dict(title='修改标题', selected_photo_ids=['a']))
    assert updated['revision'] == 5 and updated['title'] == '修改标题'


def test_no_generation_without_images_and_title_limit():
    with pytest.raises(ValueError):
        service().generate_copy({'player_name': 'x'}, 0, '', [], 'unavailable')
    generated = service().generate_copy(dict(player_name='球员'*180, product_name='Chrome'), 1, '', [], 'not_requested')
    assert len(generated['title']) <= 200


@pytest.mark.asyncio
async def test_order_examples_are_owner_scoped_bounded_and_require_canonical_source(monkeypatch):
    from card_platform import auction_api
    owner = uuid4()
    calls = []
    class Orders:
        async def status(self, conn, actual_owner):
            assert actual_owner == owner
            return dict(state='connected')
        async def archive_orders(self, conn, actual_owner):
            assert actual_owner == owner
            return None
        async def search(self, conn, actual_owner, keywords, *, prefer_live):
            calls.append((actual_owner, keywords, prefer_live))
            return dict(orders=[dict(lines=[
                dict(title='bad', item_url='https://outside.invalid/123'),
                *[dict(title=f'订单标题{i}', item_url=f'https://www.cardhobby.com.cn/market/item/{i}') for i in range(1, 8)],
            ])])
    monkeypatch.setattr(auction_api, 'account_service', Orders)
    examples, status = await auction_api.load_order_examples(None, owner, dict(player_name='测试球员'))
    assert status == 'used' and len(examples) == 3
    assert calls == [(owner, ['测试球员'], True)]
    assert all(example['item_url'].startswith('https://cardhobby.com.cn/market/item/') for example in examples)


@pytest.mark.asyncio
async def test_order_failure_is_explicit_and_never_masks_programming_errors(monkeypatch):
    from card_platform import auction_api
    class Orders:
        async def status(self, *args):
            raise TimeoutError('remote timeout')
    monkeypatch.setattr(auction_api, 'account_service', Orders)
    assert await auction_api.load_order_examples(None, uuid4(), {}) == ([], 'unavailable')
    class Broken:
        async def status(self, *args):
            raise RuntimeError('programming error')
    monkeypatch.setattr(auction_api, 'account_service', Broken)
    with pytest.raises(RuntimeError):
        await auction_api.load_order_examples(None, uuid4(), {})


@pytest.mark.asyncio
async def test_optional_order_cipher_failure_cannot_block_generation(monkeypatch):
    from card_platform import auction_api
    def unavailable():
        raise ValueError('account key permissions invalid')
    monkeypatch.setattr(auction_api, 'account_service', unavailable)
    assert await auction_api.load_order_examples(None, uuid4(), {}) == ([], 'unavailable')
