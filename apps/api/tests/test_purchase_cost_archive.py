"""Private archive behavior, independent of real credentials and accounts."""
import importlib.util
from datetime import datetime, timedelta, timezone
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image


def archive(tmp_path, owner=None):
    assert importlib.util.find_spec('card_platform.purchase_cost_archive'), 'missing durable archive'
    from card_platform.purchase_cost_archive import PurchaseArchive
    return PurchaseArchive(tmp_path, owner or uuid4())


def picture():
    out = BytesIO()
    Image.new('RGB', (32, 32), 'white').save(out, format='PNG')
    return out.getvalue()


def line():
    return {'item_id': '123', 'item_url': 'https://cardhobby.com.cn/market/item/456',
            'title': 'Fixture', 'image_urls': ['https://images.cardhobby.com/a.png']}


def test_page_checkpoint_survives_restart_but_partial_is_not_complete(tmp_path):
    owner = uuid4()
    a = archive(tmp_path, owner)
    a.save_page({'account_key': 'a'*64, 'orders': [], 'next_cursor': 'page2', 'complete': False}, None)
    b = archive(tmp_path, owner)
    assert b.state()['next_cursor'] == 'page2'
    assert b.orders('a'*64) is None
    b.save_page({'account_key': 'a'*64, 'orders': [], 'next_cursor': None, 'complete': True}, 'page2')
    assert archive(tmp_path, owner).orders('a'*64) == []


def test_account_change_and_duplicate_cursor_cannot_mix_or_complete(tmp_path):
    a = archive(tmp_path)
    a.save_page({'account_key': 'a'*64, 'orders': [], 'next_cursor': 'page2', 'complete': False}, None)
    with pytest.raises(ValueError):
        a.save_page({'account_key': 'b'*64, 'orders': [], 'next_cursor': None, 'complete': True}, 'page2')
    with pytest.raises(ValueError):
        a.save_page({'account_key': 'a'*64, 'orders': [], 'next_cursor': 'page2', 'complete': False}, 'page2')
    assert a.state()['complete'] is False


def test_order_repeated_on_different_pages_signals_drift_not_complete_history(tmp_path):
    from test_purchase_cost_postgres import order
    a = archive(tmp_path)
    a.save_page({'account_key':'a'*64,'orders':[order()], 'next_cursor':'page2','complete':False},None)
    with pytest.raises(ValueError, match='repeated'):
        a.save_page({'account_key':'a'*64,'orders':[order()], 'next_cursor':None,'complete':True},'page2')
    assert a.state()['complete'] is False


def test_owner_isolation_and_old_snapshot_never_claims_fresh_complete(tmp_path):
    a, b = archive(tmp_path), archive(tmp_path)
    a.save_page({'account_key': 'a'*64, 'orders': [], 'next_cursor': None, 'complete': True}, None)
    assert b.orders('a'*64) is None
    assert a.orders('b'*64) is None
    future = datetime.now(timezone.utc) + timedelta(days=2)
    assert a.orders('a'*64, now=future) is None


def test_gallery_bytes_are_private_verified_and_bound_to_exact_listing(tmp_path):
    a = archive(tmp_path)
    gallery = {'image_urls': line()['image_urls'], 'source_url': line()['item_url']}
    a.save_gallery(line(), gallery, [picture()])
    assert a.gallery(line())[1] == [picture()]
    other = {**line(), 'item_url': 'https://cardhobby.com.cn/market/item/999'}
    assert a.gallery(other) is None
    assert all(p.stat().st_mode & 0o077 == 0 for p in a.root.rglob('*'))
    blob = next((a.root/'blobs').glob('*'))
    blob.write_bytes(b'corrupted')
    assert a.gallery(line()) is None


def test_unsafe_image_and_oversize_gallery_never_become_cached_evidence(tmp_path):
    a = archive(tmp_path)
    for urls in [['https://evil.test/a.png'], line()['image_urls']*7]:
        with pytest.raises(ValueError):
            a.save_gallery(line(), {'image_urls': urls, 'source_url': line()['item_url']}, [picture()]*len(urls))
    assert a.gallery(line()) is None


def test_pair_observation_is_atomic_private_and_cannot_cross_owner(tmp_path):
    a, b = archive(tmp_path), archive(tmp_path)
    a.write('pairs', 'a'*64, {'verdict': 'same'})
    assert a.read('pairs', 'a'*64)['verdict'] == 'same'
    assert b.read('pairs', 'a'*64) is None
    with pytest.raises(ValueError):
        a.write('pairs', '../outside', {})


def test_configured_private_root_cannot_be_served_as_an_uploaded_image(tmp_path):
    from card_platform.config import Settings
    from card_platform.purchase_cost_archive import PurchaseArchive
    settings = Settings(upload_dir=str(tmp_path/'uploads'),
                        purchase_cost_archive_dir=str(tmp_path/'uploads'/'private'))
    with pytest.raises(ValueError, match='uploads'):
        PurchaseArchive.configured(settings, uuid4())


def test_resumed_pages_keep_oldest_capture_time_and_cannot_renew_old_orders(monkeypatch, tmp_path):
    from card_platform import purchase_cost_archive as module

    class Clock(datetime):
        current = datetime(2026, 10, 17, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(module, 'datetime', Clock)
    a = archive(tmp_path)
    first = a.save_page(dict(account_key='a'*64, orders=[], next_cursor='page2', complete=False), None)
    Clock.current += timedelta(days=2)
    finished = a.save_page(dict(account_key='a'*64, orders=[], next_cursor=None, complete=True), 'page2')
    assert finished['captured_at'] == first['captured_at']
    assert a.orders('a'*64) is None
    assert a.orders('a'*64, require_fresh=False) == []
