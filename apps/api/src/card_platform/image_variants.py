"""Private derivative generation. Callers must authorize the current source first."""
from contextlib import contextmanager
from hashlib import file_digest, sha256
from pathlib import Path
import threading
import time
from typing import Literal
from uuid import uuid4

import cv2
import numpy as np

from card_platform.media_validation import normalize_media_content, validate_image_header

ImageVariant = Literal['thumb', 'preview', 'original']
# One generation at a time also coalesces simultaneous misses for the same key.
_GENERATION_LOCK = threading.Lock()
GENERATION_WAIT_SECONDS = 5
MAX_CACHE_BYTES = 512 * 1024 * 1024
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def image_revision(image_url: str | None) -> str | None:
    if not image_url or not image_url.startswith('/uploads/'):
        return None
    filename = image_url.removeprefix('/uploads/')
    if not filename or filename in {'.', '..'} or any(c in filename for c in '/\\\x00'):
        return None
    return filename


def atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def get_image_variant(source_path: Path, cache_root: Path, cache_key: str, variant: ImageVariant) -> Path:
    if variant not in {'thumb', 'preview', 'original'}:
        raise ValueError('unsupported image variant')
    # Deleted sources never become accessible merely because a derivative exists.
    if source_path.is_symlink() or not source_path.is_file():
        raise FileNotFoundError(source_path)
    validate_image_header(source_path)
    if variant == 'original':
        return source_path
    with source_path.open('rb') as handle:
        source_hash = file_digest(handle, 'sha256').hexdigest()
    digest = sha256(f'v2:{source_path.resolve()}:{cache_key}:{source_hash}:{variant}'.encode()).hexdigest()
    destination = cache_root / f'{digest}.jpg'
    with generation_slot():
        if cache_root.is_symlink() or destination.is_symlink():
            raise FileNotFoundError(destination)
        if destination.is_file():
            return destination
        processing, _, _ = normalize_media_content(source_path.read_bytes())
        image = cv2.imdecode(np.frombuffer(processing, np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[0] * image.shape[1] > 40_000_000:
            raise ValueError('image cannot be decoded')
        edge = 320 if variant == 'thumb' else 960
        height, width = image.shape[:2]
        scale = min(1, edge / max(width, height))
        if scale < 1:
            image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
        success, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if not success:
            raise ValueError('image encoding failed')
        cache_root.mkdir(parents=True, exist_ok=True)
        _prune_cache(cache_root, encoded.nbytes)
        atomic_write(destination, encoded.tobytes())
        return destination


@contextmanager
def generation_slot():
    if not _GENERATION_LOCK.acquire(timeout=GENERATION_WAIT_SECONDS):
        raise TimeoutError('image generation busy')
    try:
        yield
    finally:
        _GENERATION_LOCK.release()


def _prune_cache(root: Path, incoming_bytes: int) -> None:
    """Bound expendable derivatives on cache misses; source uploads are never pruned."""
    if incoming_bytes > MAX_CACHE_BYTES:
        raise OSError('image derivative exceeds cache quota')
    files = sorted((p for p in root.glob('*.jpg') if not p.is_symlink()), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    cutoff = time.time() - CACHE_TTL_SECONDS
    for path in files:
        stat = path.stat()
        if stat.st_mtime < cutoff or total + incoming_bytes > MAX_CACHE_BYTES:
            path.unlink(missing_ok=True)
            total -= stat.st_size
