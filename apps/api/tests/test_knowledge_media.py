import hashlib
import importlib.util
from io import BytesIO

import httpx
import pytest
from PIL import Image


def media_module():
    assert importlib.util.find_spec('card_platform.knowledge_media'), 'media collector is not implemented'
    from card_platform import knowledge_media
    return knowledge_media


def product():
    return {'source_key': 'topps-example', 'title': '2025-26 Topps Chrome Updates Basketball',
            'publisher': 'Topps', 'product': 'Topps Chrome Updates', 'season_start': 2025,
            'season_end': 2026, 'source_url': 'https://www.topps.com/checklist.pdf'}


def png(width=300, height=420):
    buf = BytesIO()
    Image.new('RGB', (width, height), '#456789').save(buf, 'PNG')
    return buf.getvalue()


def test_extracts_original_lazy_images_but_rejects_packaging_and_logos():
    m = media_module()
    html = '''<h1>2025-26 Topps Chrome Updates Basketball</h1>
    <img src="/logo.png" alt="Topps logo">
    <img data-src="/CooperFlagg_FR.jpg" src="placeholder.gif" alt="Rookie Debut Patch Autographs">
    <img src="/hobby-box.jpg" alt="Hobby Box">
    <picture><source srcset="/card_BK.jpg 900w, /card_BK-small.jpg 300w"></picture>'''
    images = m.extract_media_candidates(html, 'https://www.topps.com/pages/example')
    assert {x['image_url'] for x in images} == {
        'https://www.topps.com/CooperFlagg_FR.jpg', 'https://www.topps.com/card_BK.jpg'}
    assert {x['side'] for x in images} == {'front', 'back'}


def test_product_binding_rejects_other_season_and_sapphire_sibling():
    m = media_module()
    assert m.product_binding(product(), '2025/26 Topps Chrome Updates Basketball')
    assert not m.product_binding(product(), '2024-25 Topps Chrome Updates Basketball')
    assert not m.product_binding(product(), '2025-26 Topps Chrome Updates Basketball Sapphire Edition')


def test_product_binding_uses_source_proven_canonical_title_without_rewriting_raw_title():
    m = media_module()
    p = product()
    p['title'] = '2025-26 Topps Hopps Basketball'
    p['canonical_title'] = '2025-26 Topps Hoops Basketball'
    assert m.product_binding(p, '2025/26 TOPPS NBA HOOPS BASKETBALL')
    assert p['title'] == '2025-26 Topps Hopps Basketball'


def test_sibling_word_in_parallel_description_does_not_change_product_heading():
    m = media_module()
    p = product()
    p.update(title='2024-25 Panini Court Kings Basketball', season_start=2024, season_end=2025)
    assert m.product_binding(p, 'PANINI AMERICA, INC.\n2024-25 COURT KINGS BASKETBALL\n'
                             'NBA TRADING CARDS HOBBY\nCopyright Panini America\n'
                             'Legacy Portrait Signatures Sapphire is a parallel')


def test_side_is_unknown_without_explicit_front_back_evidence():
    m = media_module()
    images = m.extract_media_candidates('<img src="/design.jpg" alt="Chrome Autographs">',
                                        'https://www.topps.com/example')
    assert images[0]['side'] == 'unknown'
    assert images[0]['image_type'] == 'unknown'


def test_invalid_html_payload_cannot_be_saved_as_image():
    m = media_module()
    with pytest.raises(ValueError, match='invalid_image'):
        m.validate_image(b'<html>access denied</html>')
    assert m.validate_image(png())['width'] == 300


def test_oversized_response_is_bounded_and_not_cached(tmp_path):
    m = media_module()
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b'x' * 2048)))
    with pytest.raises(ValueError, match='size_limit'):
        m.fetch_capture('https://www.topps.com/a.pdf', tmp_path, client=client, max_bytes=1024)
    assert not list(tmp_path.glob('*.bin'))


def test_http_failure_keeps_captured_challenge_and_status(tmp_path):
    m = media_module()
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(403, content=b'<html>Just a moment</html>')))
    result = m.fetch_capture('https://www.topps.com/a', tmp_path, client=client)
    assert result['status_code'] == 403
    assert result['sha256'] == hashlib.sha256(b'<html>Just a moment</html>').hexdigest()
    assert result['path']


def test_resume_verifies_bytes_and_redownloads_corrupt_cache(tmp_path):
    m = media_module()
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=png())))
    first = m.fetch_capture('https://www.topps.com/a.png', tmp_path, client=client)
    from pathlib import Path
    Path(first['path']).write_bytes(b'broken')
    second = m.fetch_capture('https://www.topps.com/a.png', tmp_path, client=client)
    assert Path(second['path']).read_bytes() == png()
    assert second['sha256'] == first['sha256']


def test_pdf_extraction_requires_specific_review_locator_for_ready_status(tmp_path):
    m = media_module()
    pdf = BytesIO()
    Image.new('RGB', (300, 420), '#456789').save(pdf, 'PDF')
    candidates = m.extract_pdf_images(pdf.getvalue(), tmp_path)
    assert len(candidates) == 1
    assert candidates[0]['locator'].startswith('pdf:page=1;image=0;')
    assert candidates[0]['status'] == 'unverified_candidate'
    assert candidates[0]['side'] == 'unknown'


def test_pdf_resume_repairs_corrupt_extracted_original(tmp_path):
    m = media_module()
    pdf = BytesIO()
    Image.new('RGB', (300, 420), '#456789').save(pdf, 'PDF')
    candidate = m.extract_pdf_images(pdf.getvalue(), tmp_path)[0]
    from pathlib import Path
    path = Path(candidate['path'])
    path.write_bytes(b'broken')
    resumed = m.extract_pdf_images(pdf.getvalue(), tmp_path)[0]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == resumed['sha256']


def test_collection_excludes_unreviewed_pdf_and_keeps_unknown_identity(tmp_path):
    m = media_module()
    pdf = BytesIO()
    Image.new('RGB', (300, 420), '#456789').save(pdf, 'PDF')
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=pdf.getvalue(),
                                      headers={'Content-Type': 'application/pdf'})))
    p = product()
    companion = tmp_path / 'product.html'
    companion.write_text('<h1>2025-26 Topps Chrome Updates Basketball</h1>'
                         '<a href="https://cdn.shopify.com/a.pdf">Education sheet</a>')
    pages = [{'source_key': p['source_key'], 'url': 'https://cdn.shopify.com/a.pdf',
              'approved_source_sha256': hashlib.sha256(pdf.getvalue()).hexdigest(),
              'binding_artifact_path': str(companion),
              'binding_artifact_sha256': hashlib.sha256(companion.read_bytes()).hexdigest(),
              'binding_artifact_url': 'https://www.topps.com/pages/example',
              'binding_text': p['title'], 'approved_images': {'1:0': {
                  'role': 'card_reference', 'side': 'unknown', 'caption': 'Official design example',
                  'image_type': 'official_design', 'basis': 'visual review of captured PDF image'}}}]
    result = m.collect_media([p], pages, tmp_path, client=client)
    assert result['asset_count'] == 1
    import json
    row = json.loads((tmp_path / 'assets.jsonl').read_text().splitlines()[0])
    assert row['status'] == 'ready'
    assert row['side'] == 'unknown'
    assert row['image_type'] == 'official_design'
    assert row['card_number'] is None and row['player'] is None
    assert row['source_artifact_sha256']
    assert row['locator'].startswith('pdf:page=1;image=0;')
    again = m.collect_media([p], pages, tmp_path, client=client)
    assert again['asset_count'] == 1
    pages[0]['approved_source_sha256'] = '0' * 64
    changed = m.collect_media([p], pages, tmp_path, client=client)
    assert changed['asset_count'] == 0
    assert changed['products'][0]['sources'][0]['status'] == 'review_source_hash_mismatch'
    pages[0]['approved_source_sha256'] = hashlib.sha256(pdf.getvalue()).hexdigest()
    del pages[0]['binding_artifact_path']
    unbound = m.collect_media([p], pages, tmp_path, client=client)
    assert unbound['asset_count'] == 0
    assert unbound['products'][0]['sources'][0]['status'] == 'product_binding_unverified'
    render = tmp_path / 'heading.png'
    render.write_bytes(png())
    pages[0]['visual_binding'] = {
        'text': p['title'], 'pdf_sha256': hashlib.sha256(pdf.getvalue()).hexdigest(),
        'page': 1, 'render_path': str(render),
        'render_sha256': hashlib.sha256(render.read_bytes()).hexdigest(),
        'basis': 'Visual inspection of rendered original PDF heading',
    }
    visual = m.collect_media([p], pages, tmp_path, client=client)
    assert visual['asset_count'] == 1
    pages[0]['visual_binding']['pdf_sha256'] = '0' * 64
    stale = m.collect_media([p], pages, tmp_path, client=client)
    assert stale['asset_count'] == 0

