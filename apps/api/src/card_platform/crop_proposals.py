"""Owner-scoped, expiring crop proposals with immutable source and recipe results."""
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import time
from uuid import UUID, uuid4

import cv2
import numpy as np

from card_platform.image_rectification import apply_card_quad, rectify_card_image
from card_platform.image_variants import atomic_write, generation_slot
from card_platform.media_validation import normalize_media_content

PROPOSAL_TTL_SECONDS = 24 * 60 * 60


def validate_quad(quad) -> tuple[tuple[float, float], ...]:
    try:
        points = np.asarray(quad, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError('quad must contain four normalized points') from error
    if points.shape != (4, 2) or not np.isfinite(points).all() or (points < 0).any() or (points > 1).any():
        raise ValueError('quad must contain four finite points in 0..1')
    edges = np.roll(points, -1, axis=0) - points
    area = abs(float(np.sum(points[:, 0] * np.roll(points[:, 1], -1) - points[:, 1] * np.roll(points[:, 0], -1)))) / 2
    if area + 1e-12 < 0.01 or (np.linalg.norm(edges, axis=1) + 1e-12 < 0.02).any():
        raise ValueError('quad must cover at least 1% of the image with edges at least 2% long')
    following = np.roll(edges, -1, axis=0)
    cross = edges[:, 0] * following[:, 1] - edges[:, 1] * following[:, 0]
    if (cross <= 1e-6).any():
        raise ValueError('quad must be convex and clockwise')
    # Canonical corner names in image coordinates, with a stable topmost tie break.
    start = min(range(4), key=lambda i: (points[i].sum(), points[i, 1]))
    if start != 0:
        raise ValueError('quad must start at the top-left corner')
    return tuple((float(p[0]), float(p[1])) for p in points)


class CropProposalStore:
    def __init__(self, root: Path):
        self.root = root

    def _directory(self, owner: UUID, proposal_id) -> Path:
        try:
            return self.root / str(UUID(str(owner))) / str(UUID(str(proposal_id)))
        except ValueError as error:
            raise FileNotFoundError('proposal not found') from error

    def _load(self, owner: UUID, proposal_id) -> tuple[Path, dict]:
        directory = self._directory(owner, proposal_id)
        if self.root.is_symlink() or directory.is_symlink() or directory.parent.is_symlink():
            raise FileNotFoundError('proposal not found')
        metadata = directory / 'metadata.json'
        if metadata.is_symlink():
            raise FileNotFoundError('proposal not found')
        try:
            data = json.loads(metadata.read_text())
        except (OSError, ValueError) as error:
            raise FileNotFoundError('proposal not found') from error
        if data['expires_at'] <= time.time():
            shutil.rmtree(directory)
            raise FileNotFoundError('proposal expired')
        return directory, data

    def create(self, owner: UUID, content: bytes, content_type: str, target: str = 'card') -> dict:
        with generation_slot():
            return self._create(owner, content, content_type, target)

    def _create(self, owner: UUID, content: bytes, content_type: str, target: str) -> dict:
        if target not in {'card', 'slab'}:
            raise ValueError('target must be card or slab')
        self.prune_expired()
        processing, width, height = normalize_media_content(content)
        image = cv2.imdecode(np.frombuffer(processing, np.uint8), cv2.IMREAD_COLOR)
        if image is None or min(image.shape[:2]) < 2 or image.shape[0] * image.shape[1] > 40_000_000:
            raise ValueError('image is invalid or exceeds 40 megapixels')
        rectified = rectify_card_image(processing, content_type, standardize_ratio=False)
        quad = rectified.normalized_quad if target == 'card' else None
        if quad is not None:
            try:
                quad = validate_quad(quad)
            except ValueError:
                quad = None
        proposal_id = uuid4()
        directory = self._directory(owner, proposal_id)
        directory.mkdir(parents=True)
        atomic_write(directory / 'source', content)
        metadata = {
            'proposal_id': str(proposal_id), 'source_hash': sha256(content).hexdigest(),
            'width': width, 'height': height, 'content_type': content_type,
            'target': target, 'algorithm_version': 'card-quad-v1', 'target_semantics': 'framing_hint',
            'confidence': float(rectified.confidence), 'expires_at': time.time() + PROPOSAL_TTL_SECONDS,
        }
        atomic_write(directory / 'metadata.json', json.dumps(metadata).encode())
        return self._apply(owner, proposal_id, quad, automatic=True)

    def apply(self, owner: UUID, proposal_id, quad) -> dict:
        # Reject an inaccessible proposal before waiting for the image worker.
        self._load(owner, proposal_id)
        with generation_slot():
            return self._apply(owner, proposal_id, quad)

    def _apply(self, owner: UUID, proposal_id, quad, *, automatic: bool = False) -> dict:
        directory, data = self._load(owner, proposal_id)
        normalized = validate_quad(quad) if quad is not None else None
        source = self.resource(owner, proposal_id, 'source').read_bytes()
        processing, _, _ = normalize_media_content(source)
        content = apply_card_quad(processing, data['content_type'], normalized).content if normalized else source
        revision = sha256(json.dumps({'source': data['source_hash'], 'quad': normalized, 'target': data['target'], 'algorithm_version': data['algorithm_version']}, sort_keys=True).encode()).hexdigest()
        atomic_write(directory / revision, content)
        base = f"/v1/capture/crop-proposals/{data['proposal_id']}/images"
        return {key: data[key] for key in ('proposal_id', 'source_hash', 'width', 'height', 'confidence', 'expires_at', 'target', 'algorithm_version', 'target_semantics')} | {
            'quad': normalized, 'source_url': f'{base}/source', 'preview_url': f'{base}/{revision}',
            'result_revision': revision,
            'status': ('proposed' if normalized else 'manual_required') if automatic else ('manual_applied' if normalized else 'original_retained'),
        }

    def prune_expired(self) -> None:
        """Reclaim expired proposal sources and results during subsequent uploads."""
        now = time.time()
        if self.root.is_symlink():
            raise FileNotFoundError('proposal store not found')
        for metadata in self.root.glob('*/*/metadata.json'):
            if metadata.is_symlink() or metadata.parent.is_symlink() or metadata.parent.parent.is_symlink():
                continue
            try:
                data = json.loads(metadata.read_text())
                if data.get('expires_at', now + 1) <= now:
                    shutil.rmtree(metadata.parent)
            except (OSError, ValueError):
                continue

    def resource(self, owner: UUID, proposal_id, resource: str) -> Path:
        directory, _ = self._load(owner, proposal_id)
        if resource != 'source' and re.fullmatch(r'[0-9a-f]{64}', resource) is None:
            raise FileNotFoundError('image not found')
        path = directory / resource
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError('image not found')
        return path
