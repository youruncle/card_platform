"""Fetch only public market-image facts into a resumable discovery corpus."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit

from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url
from card_platform.public_image_index import MAX_BYTES


MIME_TYPES = {'image/jpeg', 'image/png', 'image/webp'}


def public_source_pair(row):
    source = canonical_public_item_url(row.get('source_url'))
    image = row.get('image_url')
    try:
        url = urlsplit(image)
        transform = not url.query or bool(re.fullmatch(
            r'imageView2/\d+(?:/[a-z]+/\d+)*=?', unquote(url.query)))
        valid = (row.get('provider_code') == 'cardhobby' and source
                 and url.scheme == 'https' and url.netloc == 'images.cardhobby.com'
                 and not (url.username or url.password or url.port or url.fragment)
                 and transform and bool(url.path.strip('/')))
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError('invalid_public_source_pair')
    return source, image


def _write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = stream.name
        os.chmod(temporary, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _cached(root, image):
    path = root/'receipts'/(hashlib.sha256(image.encode()).hexdigest() + '.json')
    try:
        if path.is_symlink() or path.stat().st_size > 16384:
            return None
        receipt = json.loads(path.read_text())
        sha = receipt['sha256']
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
            return None
        blob = root/'blobs'/(sha + '.blob')
        captured = datetime.fromisoformat(receipt['captured_at'])
        if (receipt['image_url'] != image or receipt['content_type'] not in MIME_TYPES
                or captured.tzinfo is None or captured > datetime.now(timezone.utc)
                or blob.is_symlink() or not 0 < blob.stat().st_size <= MAX_BYTES):
            return None
        data = blob.read_bytes()
        return (receipt, data) if hashlib.sha256(data).hexdigest() == sha else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def ingest_one(index, row, asset_root, client):
    """The caller loads row from public market evidence; no private image input."""
    source, image = public_source_pair(row)
    if index.db.execute('SELECT 1 FROM sources WHERE source_url=? AND image_url=?',
                        (source, image)).fetchone():
        return {'status': 'already_indexed', 'source_url': source, 'image_url': image}
    root = Path(asset_root)
    for directory in (root, root/'blobs', root/'receipts'):
        if directory.is_symlink():
            raise ValueError('asset_directory_symlink')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    cached = _cached(root, image)
    if cached:
        receipt, content = cached
    else:
        with client.stream('GET', image, follow_redirects=False) as response:
            response.raise_for_status()
            mime = response.headers.get('content-type', '').split(';')[0].strip().lower()
            if mime not in MIME_TYPES:
                raise ValueError('source_response_not_image')
            length = response.headers.get('content-length')
            if length and int(length) > MAX_BYTES:
                raise ValueError('source_image_too_large')
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError('source_image_too_large')
            content = bytes(data)
        receipt = {'image_url': image, 'sha256': hashlib.sha256(content).hexdigest(),
                   'captured_at': datetime.now(timezone.utc).isoformat(), 'content_type': mime}
        # Decode before saving a successful receipt. Upsert also revalidates
        # exact bytes and performs the sole persistent feature extraction.
        import cv2
        import numpy as np
        decoded = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_GRAYSCALE) if content else None
        if decoded is None or decoded.size > 40_000_000:
            raise ValueError('invalid_image_dimensions')
        _write(root/'blobs'/(receipt['sha256'] + '.blob'), content)
        _write(root/'receipts'/(hashlib.sha256(image.encode()).hexdigest() + '.json'),
               json.dumps(receipt, ensure_ascii=False, indent=2).encode())
    status = index.upsert({'provider': 'cardhobby', 'source_url': source, **receipt}, content)
    return {'status': status, 'source_url': source, **receipt,
            'evidence_id': str(row.get('id') or ''), 'evidence_version': row.get('evidence_version'),
            'source_fetched_at': str(row.get('fetched_at') or ''), 'used_cached_original': bool(cached)}
