"""Guard the candidate/verified boundary and real source parsing behavior."""
import gzip
import hashlib
import importlib
import json
from io import BytesIO

import httpx
from PIL import Image
import pytest


def module(name):
    return importlib.import_module('card_platform.knowledge_intake_' + name)


def product(tmp_path, key, players):
    entities = [dict(id=f'{key}:{i}', entity_type='card', attributes=dict(
        brand='Topps', product='Topps Chrome', season_start=2025, season_end=2026,
        player=player, card_number=str(i), insert=None, section='BASE CARDS',
        source_key=key, source_url='https://www.topps.com/checklist.pdf',
        source_locator=f'page 1 line {i}', source_checksum='a' * 64,
    )) for i, player in enumerate(players, 1)]
    raw = gzip.compress(json.dumps(entities).encode())
    (tmp_path / f'{key}.gz').write_bytes(raw)
    return dict(id=key, canonical_name=key, entity_type='product', attributes=dict(
        source_key=key, manufacturer='Topps', season_start=2025, season_end=2026,
        official_artifact=dict(path=f'{key}.gz', checksum=hashlib.sha256(raw).hexdigest(),
                               card_count=len(entities), source_key=key),
        reference_images=[],
    ))


def test_pilot_round_robins_products_and_keeps_official_identity(tmp_path):
    a, b = product(tmp_path, 'a', ['Alpha', 'Beta']), product(tmp_path, 'b', ['Gamma'])
    result = module('worker').plan_targets([a, b], tmp_path, limit=2)
    assert [t['identity']['player_name'] for t in result['targets']] == ['Alpha', 'Gamma']
    assert result['catalog_records'] == 3 and result['selected_targets'] == 2
    assert result['targets'][0]['official_evidence']['source_locator'] == 'page 1 line 1'
    assert result['targets'][0]['missing_sides'] == ['front', 'back']


def test_verified_front_does_not_count_as_back_or_complete_pair(tmp_path):
    a = product(tmp_path, 'a', ['Alpha'])
    a['attributes']['reference_images'] = [dict(side='front', identity_verification={
        'status': 'verified', 'card_id': 'a:1'})]
    result = module('worker').plan_targets([a], tmp_path, limit=10)
    assert result['targets'][0]['missing_sides'] == ['back']
    a['attributes']['reference_images'].append(dict(side='back', identity_verification={
        'status': 'verified', 'card_id': 'a:1'}))
    assert module('worker').plan_targets([a], tmp_path, limit=10)['targets'] == []


def test_unverified_product_image_does_not_satisfy_exact_card_gap(tmp_path):
    a = product(tmp_path, 'a', ['Alpha'])
    a['attributes']['reference_images'] = [dict(side=s, player='Alpha', card_number='1')
                                          for s in ['front', 'back']]
    assert module('worker').plan_targets([a], tmp_path, limit=10)['selected_targets'] == 1


def test_bad_artifact_hash_stops_planning(tmp_path):
    a = product(tmp_path, 'a', ['Alpha'])
    (tmp_path / 'a.gz').write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        module('worker').plan_targets([a], tmp_path, limit=1)


def test_empty_shell_is_an_error_not_no_results():
    sources = module('sources')
    with pytest.raises(ValueError, match='unconfirmed'):
        sources.search_rows({'result': 1, 'data': {'Total': None, 'TotalCount': None,
                                                 'PageIndex': None, 'List': []}})
    assert sources.search_rows({'result': 1, 'data': {'Total': 0, 'PageIndex': 1,
                                                    'List': []}}) == []


def test_full_catalog_identity_never_relaxes_to_player_and_generic_card_number():
    queries = module('sources').source_queries(dict(player_name='Cooper Flagg', card_number='1',
        product_name='Topps Chrome', brand_name='Topps', season_start=2025))
    assert 1 <= len(queries) <= 2
    assert all('Chrome' in q for q in queries)
    assert '2025' in queries[0]


def test_insert_anchor_is_preserved_in_each_catalog_query():
    queries = module('sources').source_queries(dict(player_name='Caitlin Clark', card_number='1',
        product_name='Panini WNBA One and One', insert_name='Kaboom!', season_start=2025))
    assert all('Kaboom!' in query for query in queries)


def test_source_prioritizes_matching_release_over_newer_wrong_product():
    rows = [dict(ID=1, Title='2026 Topps Chrome Updates Cooper Flagg 1'),
            dict(ID=2, Title='2025 Bowman Cooper Flagg base 1')]
    ranked = module('sources').rank_rows(rows, dict(player_name='Cooper Flagg', card_number='1',
        product_name='Bowman', brand_name='Topps', season_start=2025, season_end=2026))
    assert ranked[0]['ID'] == 2


def test_http_error_is_captured_and_not_a_success(tmp_path):
    sources = module('sources')
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, text='blocked'))) as client:
        source = sources.CardHobbyImageSource(tmp_path, client=client, delay=0)
        result = source.capture({'player_name': 'Alpha', 'card_number': '1', 'sport': 'basketball'})
    assert result['status'] == 'blocked' and result['candidates'] == []
    capture = result['attempts'][0]['capture']
    assert (tmp_path / capture['path']).read_bytes() == b'blocked'
    assert result['attempts'][0]['request']['headers']['X-Requested-With'] == 'XMLHttpRequest'


def test_source_downloads_only_bound_gallery_and_never_labels_sides(tmp_path):
    sources = module('sources')
    out = BytesIO()
    Image.new('RGB', (120, 180), 'blue').save(out, format='JPEG')
    picture = out.getvalue()
    html = '''<h1 class="newcontitle">Alpha #1</h1>
      <a href="/market/bidrecords/123"><span id="bidcount">3</span></a>
      <div id="layer-photos-demo"><img src="https://images.cardhobby.com/front.jpg">
      <img src="https://images.cardhobby.com/back.jpg"></div>'''
    def respond(request):
        if 'SearchCommodity' in request.url.path:
            return httpx.Response(200, json={'result': 1, 'data': {'Total': 1, 'PageIndex': 1,
                'List': [{'ID': 123, 'Title': 'Alpha #1', 'TitImg': 'https://images.cardhobby.com/front.jpg'}]}})
        if request.url.host == 'images.cardhobby.com':
            return httpx.Response(200, content=picture, headers={'content-type': 'image/jpeg'})
        return httpx.Response(200, text=html)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = sources.CardHobbyImageSource(tmp_path, client=client, delay=0).capture(
            {'player_name': 'Alpha', 'card_number': '1', 'sport': 'basketball'})
    assert result['status'] == 'captured'
    candidate = result['candidates'][0]
    assert len(candidate['images']) == 2
    assert all(image['side'] == 'unknown' for image in candidate['images'])
    assert candidate['status'] == 'pending_review'
    assert candidate['source_url'] == 'https://cardhobby.com.cn/market/item/123'
    assert result['http_requests'] == 4
    receipt = json.loads((tmp_path / candidate['source_capture']['receipt_path']).read_text())
    assert receipt['request']['headers']['Referer'].startswith('https://www.cardhobby.com.cn/')


def test_cdn_redirect_to_private_address_is_not_followed(tmp_path):
    sources = module('sources')
    requests = []
    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={'location': 'http://127.0.0.1/secrets'})
    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        source = sources.CardHobbyImageSource(tmp_path, client=client, delay=0)
        with pytest.raises(ValueError, match='redirect'):
            source.fetch('https://images.cardhobby.com/allowed.jpg', kind='image')
    assert requests == ['https://images.cardhobby.com/allowed.jpg']


def test_capture_size_limit_is_enforced(tmp_path):
    sources = module('sources')
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'x' * 21))) as client:
        source = sources.CardHobbyImageSource(tmp_path, client=client, delay=0, max_bytes=20)
        with pytest.raises(ValueError, match='size_limit'):
            source.fetch('https://images.cardhobby.com/large.jpg', kind='image')


def test_later_image_failure_keeps_acquired_front_and_retryable_state(tmp_path):
    sources = module('sources')
    buffer = BytesIO()
    Image.new('RGB', (120, 180), 'blue').save(buffer, format='JPEG')
    html = '''<h1 class="newcontitle">Alpha #1</h1>
      <a href="/market/bidrecords/123"><span id="bidcount">3</span></a>
      <div id="layer-photos-demo"><img src="https://images.cardhobby.com/front.jpg">
      <img src="https://images.cardhobby.com/back.jpg"></div>'''
    def respond(request):
        if 'SearchCommodity' in request.url.path:
            return httpx.Response(200, json={'result': 1, 'data': {'Total': 1,
                'List': [{'ID': 123, 'Title': 'Alpha #1', 'TitImg': 'https://images.cardhobby.com/front.jpg'}]}})
        if request.url.path.endswith('back.jpg'):
            return httpx.Response(403, text='denied')
        if request.url.host == 'images.cardhobby.com':
            return httpx.Response(200, content=buffer.getvalue())
        return httpx.Response(200, text=html)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = sources.CardHobbyImageSource(tmp_path, client=client, delay=0).capture(
            {'player_name': 'Alpha', 'card_number': '1', 'sport': 'basketball'})
    assert result['status'] == 'error'
    assert len(result['candidates']) == 1
    candidate = result['candidates'][0]
    assert len(candidate['images']) == 1
    assert candidate['acquisition_failures'][0]['capture']['status_code'] == 403
    assert candidate['status'] == 'pending_review'


def reviewed_candidate(tmp_path):
    p = product(tmp_path, 'a', ['Alpha'])
    target = module('worker').plan_targets([p], tmp_path, limit=1)['targets'][0]
    html = '''<h1 class="newcontitle">Alpha Chrome #1</h1>
      <a href="/market/bidrecords/123"><span id="bidcount">3</span></a>
      <div id="layer-photos-demo"><img src="https://images.cardhobby.com/front.jpg">
      <img src="https://images.cardhobby.com/back.jpg"></div>'''
    images = []
    for index, colour in enumerate(['blue', 'red']):
        stream = BytesIO()
        Image.new('RGB', (120, 180), colour).save(stream, format='JPEG')
        data = stream.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        path = tmp_path / 'captures' / (digest + '.bin')
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        images.append(dict(path=f'captures/{digest}.bin', sha256=digest, width=120,
                           height=180, format='JPEG', side='unknown',
                           url=f'https://images.cardhobby.com/{["front", "back"][index]}.jpg'))
    data = html.encode()
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / 'captures' / (digest + '.bin')).write_bytes(data)
    source = dict(path=f'captures/{digest}.bin', sha256=digest,
                  captured_at='2026-10-22T00:00:00+00:00', status_code=200)
    payload = dict(platform='cardhobby', source_url='https://cardhobby.com.cn/market/item/123',
                   item_id='123', title='Alpha Chrome #1', status='pending_review',
                   source_capture=source, images=images,
                   source_gallery={'primary_image_url': images[0]['url']})
    from card_platform.knowledge_intake_sources import content_hash
    payload['content_sha256'] = content_hash(payload)
    candidate = dict(content_sha256=payload['content_sha256'], payload=payload,
                     target=target, decision='accepted')
    review = dict(candidate_sha256=payload['content_sha256'], decision='accepted',
                  reviewer='test-auditor', reason='Both card faces independently examined',
                  identity=target['identity'],
                  identity_evidence=dict(player_name='Alpha on both faces', card_number='back: 1',
                                         product_name='Topps Chrome on back', season_start='back: 2025-26'),
                  images=[dict(index=i, sha256=im['sha256'], decision='accepted',
                               side=['front', 'back'][i], observations='visible card face reviewed')
                          for i, im in enumerate(images)])
    candidate['review'] = review
    return candidate, p


def test_pending_candidate_cannot_be_exported(tmp_path):
    candidate, _ = reviewed_candidate(tmp_path)
    candidate['decision'] = 'pending_review'
    with pytest.raises(ValueError, match='unreviewed'):
        module('worker').export_assets([candidate], tmp_path, tmp_path / 'export')


def test_changed_image_invalidates_review_before_export(tmp_path):
    candidate, _ = reviewed_candidate(tmp_path)
    (tmp_path / candidate['payload']['images'][0]['path']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        module('worker').export_assets([candidate], tmp_path, tmp_path / 'export')


def test_review_cannot_silently_rebind_to_other_player(tmp_path):
    candidate, _ = reviewed_candidate(tmp_path)
    candidate['review']['identity'] = {**candidate['target']['identity'], 'player_name': 'Someone Else'}
    with pytest.raises(ValueError, match='identity'):
        module('worker').validate_review(candidate, candidate['review'], tmp_path)


@pytest.mark.parametrize('field,value', [('insert_name', 'Inspirational'),
                                       ('parallel_name', 'Gold'), ('serial_total', 10),
                                       ('section', 'Caitlin Clark WNBA Green Holo')])
def test_explicit_version_requires_its_own_visible_evidence(tmp_path, field, value):
    candidate, _ = reviewed_candidate(tmp_path)
    candidate['target']['identity'] = {**candidate['target']['identity'], field: value}
    candidate['review']['identity'] = dict(candidate['target']['identity'])
    with pytest.raises(ValueError, match='version_evidence'):
        module('worker').validate_review(candidate, candidate['review'], tmp_path)


def test_numbered_version_rejects_a_conflicting_visible_serial(tmp_path):
    candidate, _ = reviewed_candidate(tmp_path)
    candidate['target']['identity'] = {**candidate['target']['identity'], 'serial_total': 10}
    candidate['review']['identity'] = dict(candidate['target']['identity'])
    candidate['review']['identity_evidence']['serial_total'] = 'front stamp 5/99'
    with pytest.raises(ValueError, match='visible_serial'):
        module('worker').validate_review(candidate, candidate['review'], tmp_path)


def test_unpaired_export_retains_exact_card_binding_without_inventing_pair(tmp_path):
    from card_platform.knowledge_release import attach_reference_images
    candidate, p = reviewed_candidate(tmp_path)
    result = module('worker').export_assets([candidate], tmp_path, tmp_path / 'export')
    assets = [json.loads(line) for line in (tmp_path / 'export/assets.jsonl').read_text().splitlines()]
    assert result['images'] == 2
    manifest = dict(sources=[dict(key='a')], entities=[p])
    attached = attach_reference_images(manifest, assets, tmp_path)
    refs = attached['entities'][0]['attributes']['reference_images']
    assert {r['side'] for r in refs} == {'front', 'back'}
    assert all(r['player'] == 'Alpha' and r['card_number'] == '1' for r in refs)
    assert all(r['pair_id'] is None for r in refs)
    assert attached['entities'][0]['attributes']['knowledge_media_summary']['paired_cards'] == 0


def test_single_reviewed_front_can_be_exported_while_back_is_rejected(tmp_path):
    candidate, _ = reviewed_candidate(tmp_path)
    candidate['review']['images'][1].update(decision='rejected', side='unknown',
                                           observations='not a usable card back')
    result = module('worker').export_assets([candidate], tmp_path, tmp_path / 'export')
    assert result['images'] == 1


def test_unpaired_contract_rejects_a_fabricated_pair(tmp_path):
    from card_platform.knowledge_release import attach_reference_images
    candidate, p = reviewed_candidate(tmp_path)
    module('worker').export_assets([candidate], tmp_path, tmp_path / 'export')
    assets = [json.loads(line) for line in (tmp_path / 'export/assets.jsonl').read_text().splitlines()]
    assets[0]['pair_id'] = 'invented'
    with pytest.raises(ValueError, match='cannot_claim_pair'):
        attach_reference_images(dict(sources=[dict(key='a')], entities=[p]), assets, tmp_path)


def test_reviewed_source_without_original_gallery_is_rejected_at_publication(tmp_path):
    from card_platform.knowledge_release import attach_reference_images
    candidate, p = reviewed_candidate(tmp_path)
    module('worker').export_assets([candidate], tmp_path, tmp_path / 'export')
    assets = [json.loads(line) for line in (tmp_path / 'export/assets.jsonl').read_text().splitlines()]
    assets[0]['image_url'] = 'https://images.cardhobby.com/not-in-original.jpg'
    with pytest.raises(ValueError, match='bind_this_card_face'):
        attach_reference_images(dict(sources=[dict(key='a')], entities=[p]), assets, tmp_path)
