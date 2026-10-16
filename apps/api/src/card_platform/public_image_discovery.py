"""Local public-image discovery on one bounded reader thread, never approval."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import hashlib
from pathlib import Path
import sqlite3

import cv2

from card_platform.public_image_index import PublicImageIndex, MAX_BYTES


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='public-image-discovery')
_reader = None
_reader_key = None


def _query(index_path, target_path, expected_sha):
    global _reader, _reader_key
    index_path, target_path = Path(index_path), Path(target_path)
    if not index_path.is_file():
        return {'status': 'missing_index', 'leads': []}
    if index_path.is_symlink():
        return {'status': 'invalid_index_path', 'leads': []}
    if not target_path.is_file() or target_path.stat().st_size > MAX_BYTES:
        return {'status': 'invalid_target', 'leads': []}
    with target_path.open('rb') as stream:
        content = stream.read(MAX_BYTES + 1)
    if not content or len(content) > MAX_BYTES:
        return {'status': 'invalid_target', 'leads': []}
    sha = hashlib.sha256(content).hexdigest()
    if expected_sha and sha != expected_sha:
        return {'status': 'target_changed', 'leads': []}
    stat = index_path.stat()
    key = (str(index_path.resolve()), stat.st_dev, stat.st_ino)
    if key != _reader_key:
        if _reader is not None:
            _reader.db.close()
        _reader = None
        _reader_key = None
        _reader = PublicImageIndex(index_path, read_only=True)
        _reader_key = key
    return {'status': 'ready', 'target_sha256': sha,
            'leads': _reader.query(content, limit=12), 'discovery_only': True}


async def query_public_images(index_path, target_path, *, expected_sha=None):
    """Failure of the optional local index must not break market retrieval.

    The caller resolves the owned target path with the existing upload guard.
    SQLite and FLANN stay on their owning thread; only public-source candidates
    return to the event loop. Target image bytes are not saved anywhere.
    """
    try:
        return await asyncio.get_running_loop().run_in_executor(
            _executor, partial(_query, index_path, target_path, expected_sha))
    except (OSError, ValueError, sqlite3.Error, cv2.error) as exc:
        return {'status': 'index_unavailable', 'error_type': type(exc).__name__, 'leads': []}
