"""Synthetic image regressions; no real collection or remote models."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import importlib.util
import json
from uuid import uuid4

import cv2
import numpy as np
import pytest


def media():
    assert importlib.util.find_spec('card_platform.crop_proposals') is not None, 'crop proposals are not implemented'
    from card_platform import crop_proposals
    return crop_proposals


def picture(width=1200, height=1800):
    pixels = np.full((height, width, 3), 100, dtype=np.uint8)
    pixels[height // 5:height // 3, width // 4:width // 2] = (10, 50, 220)
    return cv2.imencode('.png', pixels)[1].tobytes()


def test_crop_preserves_source_and_manual_geometry(tmp_path):
    service = media().CropProposalStore(tmp_path)
    owner = uuid4()
    source = picture(400, 600)
    proposal = service.create(owner, source, 'image/png')
    assert proposal['source_hash'] == sha256(source).hexdigest()
    assert service.resource(owner, proposal['proposal_id'], 'source').read_bytes() == source
    applied = service.apply(owner, proposal['proposal_id'], [[.1,.1],[.9,.1],[.9,.9],[.1,.9]])
    content = service.resource(owner, proposal['proposal_id'], applied['result_revision']).read_bytes()
    assert cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (480,320)
    assert service.resource(owner, proposal['proposal_id'], 'source').read_bytes() == source
    restored = service.apply(owner, proposal['proposal_id'], None)
    assert service.resource(owner, proposal['proposal_id'], restored['result_revision']).read_bytes() == source


@pytest.mark.parametrize('quad', [
    [[0,0],[0,1],[1,1],[1,0]],  # reversed winding
    [[0,0],[1,1],[1,0],[0,1]],  # crossing
    [[0,0],[1,0],[.2,.2],[0,1]],  # concave
    [[-.1,0],[1,0],[1,1],[0,1]],
    [[0,0],[1,0],[1,1],[float('nan'),1]],
    [[1,0],[1,1],[0,1],[0,0]],  # wrong starting corner
    [[0,0],[0,0],[1,1],[0,1]],
])
def test_invalid_manual_geometry_is_rejected(quad):
    with pytest.raises(ValueError):
        media().validate_quad(quad)


def test_proposal_ownership_ttl_and_resource_traversal(tmp_path):
    service = media().CropProposalStore(tmp_path)
    owner = uuid4()
    proposal = service.create(owner, picture(100,150), 'image/png')
    pid = proposal['proposal_id']
    with pytest.raises(FileNotFoundError):
        service.resource(uuid4(), pid, 'source')
    with pytest.raises(FileNotFoundError):
        service.resource(owner, pid, '../source')
    with pytest.raises(FileNotFoundError):
        service.resource(owner, '../../etc', 'source')
    meta = tmp_path / str(owner) / str(pid) / 'metadata.json'
    data = json.loads(meta.read_text())
    data['expires_at'] = 1
    meta.write_text(json.dumps(data))
    with pytest.raises(FileNotFoundError):
        service.resource(owner, pid, 'source')
    assert not meta.parent.exists()


def test_variants_preserve_ratio_no_upscale_atomic_singleflight(tmp_path):
    assert importlib.util.find_spec('card_platform.image_variants') is not None, 'image variants are not implemented'
    from card_platform.image_variants import get_image_variant
    source = tmp_path / 'source.png'
    source.write_bytes(picture())
    cache = tmp_path / 'cache'
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda _: get_image_variant(source, cache, 'revision-one', 'thumb'), range(8)))
    assert len(set(paths)) == 1
    assert len(list(cache.iterdir())) == 1
    assert cv2.imread(str(paths[0])).shape[:2] == (320,213)
    assert cv2.imread(str(get_image_variant(source,cache,'revision-one','preview'))).shape[:2] == (960,640)
    assert get_image_variant(source,cache,'revision-one','original') == source
    small = tmp_path / 'small.png'
    small.write_bytes(picture(50,100))
    assert cv2.imread(str(get_image_variant(small,cache,'small','thumb'))).shape[:2] == (100,50)
    source.unlink()
    with pytest.raises(FileNotFoundError):
        get_image_variant(source,cache,'revision-one','thumb')


def test_variant_cache_binds_source_content_and_times_out_when_busy(tmp_path, monkeypatch):
    from card_platform import image_variants
    source = tmp_path / 'source.png'
    source.write_bytes(picture(100,150))
    first = image_variants.get_image_variant(source,tmp_path/'cache','same-key','thumb')
    pixels = np.full((200,100,3),220,np.uint8)
    source.write_bytes(cv2.imencode('.png',pixels)[1].tobytes())
    second = image_variants.get_image_variant(source,tmp_path/'cache','same-key','thumb')
    assert second != first
    assert cv2.imread(str(second)).shape[:2] == (200,100)
    monkeypatch.setattr(image_variants, 'GENERATION_WAIT_SECONDS', 0.001)
    with image_variants._GENERATION_LOCK:
        with pytest.raises(TimeoutError):
            image_variants.get_image_variant(source,tmp_path/'cache','new-key','thumb')


def test_blank_source_does_not_claim_a_crop(tmp_path):
    source = cv2.imencode('.png',np.full((300,200,3),127,np.uint8))[1].tobytes()
    service = media().CropProposalStore(tmp_path)
    owner = uuid4()
    proposal = service.create(owner,source,'image/png')
    assert proposal['quad'] is None
    assert 0 <= proposal['confidence'] < .72
    assert service.resource(owner,proposal['proposal_id'],proposal['result_revision']).read_bytes() == source


def test_crop_generation_respects_shared_wait_budget(tmp_path, monkeypatch):
    from card_platform import image_variants
    monkeypatch.setattr(image_variants, 'GENERATION_WAIT_SECONDS', .001)
    with image_variants._GENERATION_LOCK:
        with pytest.raises(TimeoutError):
            media().CropProposalStore(tmp_path).create(uuid4(),picture(100,150),'image/png')


def test_cache_pruning_never_deletes_source(tmp_path, monkeypatch):
    from card_platform import image_variants
    source = tmp_path/'source.png'
    source.write_bytes(picture(200,300))
    cache = tmp_path/'cache'
    stale = image_variants.get_image_variant(source,cache,'stale','thumb')
    import os
    os.utime(stale,(1,1))
    latest = image_variants.get_image_variant(source,cache,'current','preview')
    assert latest.is_file() and not stale.exists() and source.exists()
    monkeypatch.setattr(image_variants,'MAX_CACHE_BYTES',1)
    with pytest.raises(OSError):
        image_variants.get_image_variant(source,cache,'too-large','thumb')
    assert source.exists()


def test_new_proposal_prunes_expired_uploads_only(tmp_path):
    service = media().CropProposalStore(tmp_path)
    owner = uuid4()
    old = service.create(owner,picture(100,150),'image/png')
    meta = tmp_path/str(owner)/old['proposal_id']/'metadata.json'
    data = json.loads(meta.read_text())
    data['expires_at'] = 1
    meta.write_text(json.dumps(data))
    current = service.create(owner,picture(100,150),'image/png')
    assert not meta.parent.exists()
    assert service.resource(owner,current['proposal_id'],'source').exists()


@pytest.mark.parametrize('quad',[
    [[.1,.1],[.15,.1],[.15,.15],[.1,.15]],
    [[.1,.1],[.9,.1],[.9,.115],[.1,.115]],
])
def test_manual_crop_rejects_tiny_area_or_short_edge(quad):
    with pytest.raises(ValueError):
        media().validate_quad(quad)


def oversized_png_header():
    import struct
    import zlib
    body = struct.pack('>IIBBBBB', 10000, 5000, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n'+struct.pack('>I',len(body))+b'IHDR'+body+struct.pack('>I',zlib.crc32(b'IHDR'+body))+b'\0\0\0\0IEND\xaeB`\x82'


def test_oversized_header_rejected_before_decoder(tmp_path, monkeypatch):
    from card_platform import crop_proposals, image_variants
    def forbidden(*args, **kwargs):
        pytest.fail('OpenCV decoder reached before oversized header rejected')
    monkeypatch.setattr(cv2, 'imdecode', forbidden)
    monkeypatch.setattr(cv2, 'imread', forbidden)
    with pytest.raises(ValueError,match='40 megapixels'):
        crop_proposals.CropProposalStore(tmp_path/'proposals').create(uuid4(), oversized_png_header(), 'image/png')
    source = tmp_path/'oversized.png'
    source.write_bytes(oversized_png_header())
    with pytest.raises(ValueError,match='40 megapixels'):
        image_variants.get_image_variant(source,tmp_path/'variants','oversized','thumb')


def test_exif_dimensions_preview_and_crop_use_display_coordinates(tmp_path):
    from io import BytesIO
    from PIL import Image
    from card_platform import image_variants
    pixels = np.zeros((300,200,3),np.uint8)
    pixels[:,:100] = [255,0,0]
    pixels[:,100:] = [0,0,255]
    raw = Image.fromarray(pixels)
    exif = Image.Exif()
    exif[274] = 6 # clockwise90: red top, blue bottom; display300x200
    out = BytesIO()
    raw.save(out,format='JPEG',quality=95,exif=exif)
    source = out.getvalue()
    service = media().CropProposalStore(tmp_path/'proposals')
    owner = uuid4()
    proposal = service.create(owner,source,'image/jpeg')
    assert (proposal['width'],proposal['height']) == (300,200)
    assert proposal['source_hash'] == sha256(source).hexdigest()
    assert service.resource(owner,proposal['proposal_id'],'source').read_bytes() == source
    cropped = service.apply(owner,proposal['proposal_id'],[[0,0],[1,0],[1,.45],[0,.45]])
    result = cv2.imread(str(service.resource(owner,proposal['proposal_id'],cropped['result_revision'])))
    assert result[20,20,2] > 200 and result[20,20,0] < 30
    saved = tmp_path/'exif.jpg'
    saved.write_bytes(source)
    preview = cv2.imread(str(image_variants.get_image_variant(saved,tmp_path/'variants','exif','preview')))
    assert preview.shape[:2] == (200,300)
    assert preview[20,20,2] > 200 and preview[180,20,0] > 200


def test_slab_proposal_preserves_full_image_and_identifies_manual_target(tmp_path):
    service = media().CropProposalStore(tmp_path)
    owner = uuid4()
    proposal = service.create(owner,picture(400,600),'image/png',target='slab')
    assert proposal['target'] == 'slab'
    assert proposal['algorithm_version'] == 'card-quad-v1'
    assert proposal['target_semantics'] == 'framing_hint'
    assert proposal['status'] == 'manual_required'
    assert proposal['quad'] is None
    assert service.resource(owner,proposal['proposal_id'],proposal['result_revision']).read_bytes() == picture(400,600)
    applied = service.apply(owner,proposal['proposal_id'],[[.1,.1],[.9,.1],[.9,.9],[.1,.9]])
    assert applied['target'] == 'slab' and applied['status'] == 'manual_applied'
