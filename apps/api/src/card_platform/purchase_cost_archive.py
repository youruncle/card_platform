"""Private owner-scoped purchase evidence. Never served by the uploads route."""
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


class PurchaseArchive:
    CONTRACT = 'purchase-private-archive-v1'
    FRESH_SECONDS = 86400

    def __init__(self, root, owner):
        self.root = Path(root).resolve() / str(UUID(str(owner)))
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    @classmethod
    def configured(cls, settings, owner):
        root = (Path(settings.purchase_cost_archive_dir).expanduser().resolve()
                if settings.purchase_cost_archive_dir
                else settings.resolved_upload_dir.parent / 'purchase-cost-archive')
        if root == settings.resolved_upload_dir or settings.resolved_upload_dir in root.parents:
            raise ValueError('private purchase archive must be outside uploads')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return cls(root, owner)

    def path(self, kind, key):
        if kind not in {'state', 'orders', 'galleries', 'products', 'pairs', 'blobs', 'runs'} or not re.fullmatch('[a-f0-9]{64}', key):
            raise ValueError('invalid private archive key')
        directory = self.root / kind
        if directory.is_symlink():
            raise ValueError('invalid private archive directory')
        directory.mkdir(mode=0o700, exist_ok=True)
        return directory / key

    def _atomic(self, path, data):
        fd, name = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    def write(self, kind, key, value):
        body = dict(contract=self.CONTRACT, saved_at=datetime.now(timezone.utc).isoformat(), value=value)
        self._atomic(self.path(kind, key), json.dumps(body, ensure_ascii=False, default=str).encode())

    def read(self, kind, key, *, max_age=None, now=None):
        path = self.path(kind, key)
        try:
            if path.is_symlink() or path.stat().st_size > 16_000_000:
                return None
            body = json.loads(path.read_text())
            age = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(body['saved_at'])).total_seconds()
            if body['contract'] != self.CONTRACT or age < 0 or (max_age is not None and age > max_age):
                return None
            return body['value']
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def state(self):
        return self.read('state', '0'*64) or dict(complete=False, next_cursor=None, pages=0, order_keys=[], seen=[])

    def has_history(self):
        path = self.path('state', '0'*64)
        return path.exists() or path.is_symlink()

    def capture_age(self, *, now=None):
        try:
            captured = datetime.fromisoformat(self.state()['captured_at'])
            return ((now or datetime.now(timezone.utc))-captured).total_seconds()
        except (ValueError, KeyError, TypeError):
            return None

    def fresh(self, *, now=None):
        age = self.capture_age(now=now)
        return age is not None and 0 <= age <= self.FRESH_SECONDS

    def reset(self):
        # Original captures and original blobs are retained; only coverage resets.
        self.write('state', '0'*64, dict(complete=False, next_cursor=None, pages=0, order_keys=[], seen=[]))

    def save_page(self, page, cursor):
        from card_platform.purchase_cost import OrderCapture
        state = self.state()
        account = page['account_key']
        if not re.fullmatch('[a-f0-9]{64}', account):
            raise ValueError('invalid account binding')
        if state['pages'] and (state.get('account_key') != account or state['next_cursor'] != cursor or state['complete']):
            raise ValueError('history account or cursor changed; restart sync')
        following = page['next_cursor']
        seen = [*state['seen'], cursor or 'start']
        if following and following in seen:
            raise ValueError('history pagination repeated')
        if page['complete'] is not (following is None):
            raise ValueError('history coverage inconsistent')
        captures = [OrderCapture.model_validate(row) for row in page['orders']]
        keys = set(state['order_keys'])
        incoming = [fingerprint([account, capture.external_order_id]) for capture in captures]
        if len(incoming) != len(set(incoming)) or keys.intersection(incoming):
            raise ValueError('history order repeated across pages; restart after pagination drift')
        for capture in captures:
            key = fingerprint([account, capture.external_order_id])
            self.write('orders', key, capture.model_dump(mode='json'))
            keys.add(key)
        self.write('state', '0'*64, dict(account_key=account, pages=state['pages']+1,
                   next_cursor=following, complete=page['complete'], order_keys=sorted(keys),
                   seen=seen, captured_at=state.get('captured_at') or datetime.now(timezone.utc).isoformat()))
        return self.state()

    def orders(self, account_key=None, *, now=None, require_fresh=True):
        state = self.state()
        if not state['complete'] or (account_key is not None and state.get('account_key') != account_key):
            return None
        age = self.capture_age(now=now)
        if age is None or (require_fresh and not 0 <= age <= self.FRESH_SECONDS):
            return None
        orders = [self.read('orders', key) for key in state['order_keys']]
        return None if any(row is None for row in orders) else orders

    @staticmethod
    def line_key(line):
        return fingerprint({key: line.get(key) for key in ('item_id', 'item_url', 'image_urls')})

    def save_gallery(self, line, gallery, images):
        from card_platform.purchase_cost_images import MAX_PHOTOS, image_url_allowed, validate_photo
        from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url
        urls = gallery.get('image_urls') or []
        if (not 1 <= len(urls) <= MAX_PHOTOS or len(urls) != len(images)
                or any(not image_url_allowed(url) for url in urls)
                or not line.get('image_urls') or line['image_urls'][0] not in urls
                or not canonical_public_item_url(line.get('item_url'))
                or gallery.get('source_url') != canonical_public_item_url(line['item_url'])):
            raise ValueError('unbound purchase gallery')
        hashes = []
        for image in images:
            validate_photo(image)
            key = hashlib.sha256(image).hexdigest()
            self._atomic(self.path('blobs', key), image)
            hashes.append(key)
        self.write('galleries', self.line_key(line), dict(gallery=gallery, image_hashes=hashes))

    def gallery(self, line):
        from card_platform.purchase_cost_images import MAX_PHOTOS, MAX_BYTES, validate_photo
        row = self.read('galleries', self.line_key(line), max_age=86400)
        if not row:
            return None
        try:
            hashes = row['image_hashes']
            if not 1 <= len(hashes) <= MAX_PHOTOS:
                return None
            images = []
            for key in hashes:
                path = self.path('blobs', key)
                if path.is_symlink() or path.stat().st_size > MAX_BYTES:
                    return None
                image = path.read_bytes()
                if hashlib.sha256(image).hexdigest() != key:
                    return None
                validate_photo(image)
                images.append(image)
            return row['gallery'], images
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def product(self, line, *, verify_images=True):
        from card_platform.purchase_cost_catalog import CONTRACT, validate_product
        row = self.read('runs', fingerprint(['product-index', self.line_key(line)]))
        if not row:
            return None
        metadata = self.read('galleries', self.line_key(line), max_age=86400)
        if not metadata:
            return None
        try:
            hashes = metadata['image_hashes']
            from card_platform.purchase_cost_images import MAX_BYTES, MAX_PHOTOS
            if not 1 <= len(hashes) <= MAX_PHOTOS:
                return None
            for key in hashes:
                path = self.path('blobs',key)
                if path.is_symlink() or not 0 < path.stat().st_size <= MAX_BYTES:
                    return None
            if verify_images:
                cached = self.gallery(line)
                if not cached:
                    return None
                hashes = [hashlib.sha256(b).hexdigest() for b in cached[1]]
            binding = row['binding']
            if (binding['contract'] != CONTRACT or binding['item_id'] != line['item_id']
                    or binding['item_url'] != line['item_url']
                    or binding['image_hashes'] != hashes):
                return None
            validate_product(row['observations'], len(hashes))
            return row
        except (OSError, ValueError, KeyError, TypeError):
            return None
