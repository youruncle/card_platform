"""Read adapters expose source identities without promoting seller claims."""
from copy import deepcopy

import pytest

from test_knowledge_browser import add_image, artifact, browser, snapshot


def source_data(tmp_path):
    data = snapshot()
    card = data['entities'].pop()
    card['id'] = 'official:source:one'
    card['attributes'].update(source_key='source', sport='basketball', authority='official_checklist',
                              source_checksum='a' * 64, source_locator='row 1',
                              raw_text='official source row', section='Base Gold', parallel='Gold',
                              serial_total=25)
    ref = artifact(tmp_path, [card])
    data['entities'][3]['attributes']['official_artifact'] = ref
    return data, card, ref


def external_image(tmp_path, data, card, ref, name='front.jpg', side='front', **changes):
    return add_image(
        tmp_path, data, name, kind='verified_card_scan', side=side,
        pair_id='listing:123', source_key='source', player='Player A', card_number='1',
        variant='Base Gold', serial_total=25,
        source_url='https://www.ebay.com/itm/123', source_artifact_sha256='b' * 64,
        review_artifact_sha256='c' * 64, usage_scope='internal_reference_research',
        rights='Public access is not a redistribution license.',
        source_authority='public_specimen_listing',
        source_card_identity={'player': 'Player A', 'card_number': '1', 'product': 'Chrome',
                              'manufacturer': 'Topps', 'season': '2025-26', 'variant': 'Gold',
                              'serial_total': 25, 'serial_number': 21},
        pair_evidence={'record_type': 'public_specimen_listing', 'record_id': '123',
                       'record_url': 'https://www.ebay.com/itm/123'},
        identity_verification={'status': 'verified', 'card_id': card['id'],
                               'candidate_card_ids': [card['id']],
                               'artifact_sha256': ref['checksum'], 'source_sha256': 'b' * 64,
                               'source_url': 'https://www.ebay.com/itm/123',
                               'source_locator': 'reviewed same-record original images'},
        **changes,
    )


def test_existing_directory_establishes_named_identity_before_images(tmp_path):
    data, card, _ = source_data(tmp_path)
    index = browser(tmp_path, data)
    value = index.card(card['id'])['card']
    assert value['atlas']['atlas_id'] == card['id']
    assert value['atlas']['identity_status'] == 'confirmed_within_official_source'
    assert value['atlas']['image_status'] == 'missing'
    counts = index.product('product')['atlas_counts']
    assert counts['backbone_records'] == counts['source_identity_confirmed'] == 1
    assert counts['identity_pending'] == counts['cross_source_confirmed_identities'] == 0
    assert counts['global_unique_atlases'] is None


def test_two_faces_from_one_external_record_map_to_one_existing_identity(tmp_path):
    data, card, ref = source_data(tmp_path)
    external_image(tmp_path, data, card, ref)
    external_image(tmp_path, data, card, ref, name='back.jpg', side='back')
    index = browser(tmp_path, data)
    detail = index.card(card['id'])
    assert detail['card']['atlas']['image_status'] == 'paired'
    assert len(detail['source_mappings']) == 2
    assert {m['role'] for m in detail['source_mappings']} == {'official_backbone', 'reviewed_external_record'}
    external = next(m for m in detail['source_mappings'] if m['role'] == 'reviewed_external_record')
    assert external['source_record_id'] == '123'
    assert external['atlas_id'] == card['id']
    assert len(external['source_asset_ids']) == 2
    assert external['assertions']['serial_number']['scope'] == 'source_record_claim'
    assert detail['card']['is_autograph'] is None
    assert index.product('product')['atlas_counts']['cross_source_confirmed_identities'] == 1


@pytest.mark.parametrize('field,value', [('variant', None), ('player', 'Other Player'),
                                       ('card_number', '2'), ('serial_total', 99)])
def test_conflicting_or_unknown_reference_identity_cannot_confirm_cross_source_mapping(tmp_path, field, value):
    data, card, ref = source_data(tmp_path)
    image = external_image(tmp_path, data, card, ref)
    image[field] = value
    index = browser(tmp_path, data)
    detail = index.card(card['id'])
    external = [m for m in detail['source_mappings'] if m['role'] != 'official_backbone']
    assert external and external[0]['mapping_status'] == 'needs_review'
    assert external[0]['atlas_id'] is None
    assert index.product('product')['atlas_counts']['cross_source_confirmed_identities'] == 0


def test_unreviewed_or_wrong_checksum_does_not_promote_a_source_claim(tmp_path):
    data, card, ref = source_data(tmp_path)
    image = external_image(tmp_path, data, card, ref)
    image['identity_verification']['source_sha256'] = 'd' * 64
    detail = browser(tmp_path, data).card(card['id'])
    assert next(m for m in detail['source_mappings'] if m['role'] != 'official_backbone')['atlas_id'] is None


def test_missing_identity_remains_pending_even_if_a_picture_exists(tmp_path):
    data, card, ref = source_data(tmp_path)
    # Existing native facts without a full source identity remain browsable.
    data['entities'].append(deepcopy(card))
    data['entities'][-1]['id'] = 'native-pending'
    data['entities'][-1]['attributes']['section'] = None
    index = browser(tmp_path, data)
    assert index.card('native-pending')['card']['atlas']['identity_status'] == 'needs_review'
    assert index.product('product')['atlas_counts']['identity_pending'] == 1


def test_same_record_conflicting_faces_downgrade_the_record_and_aggregate_count(tmp_path):
    data, card, ref = source_data(tmp_path)
    external_image(tmp_path, data, card, ref)
    back = external_image(tmp_path, data, card, ref, name='back.jpg', side='back')
    back['serial_total'] = 99
    index = browser(tmp_path, data)
    mapping = next(m for m in index.card(card['id'])['source_mappings'] if m['role'] != 'official_backbone')
    assert mapping['mapping_status'] == 'needs_review'
    assert index.product('product')['atlas_counts']['cross_source_confirmed_identities'] == 0


@pytest.mark.parametrize('bad_url', ['/private/local-source.json', 'https://[invalid'])
def test_invalid_source_links_stay_pending_without_leaking_local_paths(tmp_path, bad_url):
    data, card, ref = source_data(tmp_path)
    image = external_image(tmp_path, data, card, ref)
    image['source_url'] = bad_url
    image['identity_verification']['source_url'] = bad_url
    image['pair_evidence']['record_url'] = bad_url
    mapping = next(m for m in browser(tmp_path, data).card(card['id'])['source_mappings'] if m['role'] != 'official_backbone')
    assert mapping['mapping_status'] == 'needs_review'
    assert mapping['source_url'] is None


def test_unknown_release_year_never_establishes_a_source_identity(tmp_path):
    from card_platform.knowledge_atlas import atlas_metadata
    _, card, _ = source_data(tmp_path)
    card['attributes']['season_start'] = None
    assert atlas_metadata(card)['identity_status'] == 'needs_review'


def test_card_number_and_player_filters_show_versions_without_other_cards(tmp_path):
    data, one, _ = source_data(tmp_path)
    two, three, four = (deepcopy(one) for _ in range(3))
    two['id'] = 'official:source:two'
    two['attributes'].update(section='Base Silver', parallel='Silver', serial_total=99)
    three['id'] = 'official:source:three'
    three['attributes']['card_number'] = '2'
    four['id'] = 'official:source:four'
    four['attributes']['player'] = 'Other Player'
    data['entities'][3]['attributes']['official_artifact'] = artifact(tmp_path, [one, two, three, four])
    result = browser(tmp_path, data).cards(product_id='product', player='Player A', card_number='1')
    assert {row['id'] for row in result['items']} == {one['id'], two['id']}
    assert result['total'] == 2


@pytest.mark.parametrize('field,value', [('manufacturer', 'Other Manufacturer'),
                                       ('season', '2025'), ('variant', 'Silver')])
def test_original_source_claim_conflicts_cannot_hide_behind_a_reviewed_target_id(tmp_path, field, value):
    data, card, ref = source_data(tmp_path)
    image = external_image(tmp_path, data, card, ref)
    image['source_card_identity'][field] = value
    mappings = browser(tmp_path, data).card(card['id'])['source_mappings']
    external = next(m for m in mappings if m['role'] != 'official_backbone')
    assert external['mapping_status'] == 'needs_review'
    assert external['atlas_id'] is None


@pytest.mark.parametrize('bad_record', ['unsupported_publisher', 'wrong_record_id'])
def test_record_provenance_cannot_claim_independence_from_a_different_host_alone(tmp_path, bad_record):
    data, card, ref = source_data(tmp_path)
    image = external_image(tmp_path, data, card, ref)
    if bad_record == 'unsupported_publisher':
        url = 'https://cdn.example.com/item/123'
        image['source_url'] = image['identity_verification']['source_url'] = url
        image['pair_evidence']['record_url'] = url
    else:
        image['pair_evidence']['record_id'] = '999'
    mappings = browser(tmp_path, data).card(card['id'])['source_mappings']
    external = next(m for m in mappings if m['role'] != 'official_backbone')
    assert external['mapping_status'] == 'needs_review'
    assert external['atlas_id'] is None


@pytest.mark.parametrize('field,value', [('official_card_set', 'Base Autographs Gold'),
                                       ('record_id', 'DIFFERENT_RECORD'), ('serial_number', 26)])
def test_source_record_internal_conflicts_require_review(tmp_path, field, value):
    data, card, ref = source_data(tmp_path)
    image = external_image(tmp_path, data, card, ref)
    image['source_card_identity'][field] = value
    mappings = browser(tmp_path, data).card(card['id'])['source_mappings']
    assert next(m for m in mappings if m['role'] != 'official_backbone')['atlas_id'] is None


def test_face_serial_number_cannot_conflict_with_captured_record(tmp_path):
    data, card, ref = source_data(tmp_path)
    image = external_image(tmp_path, data, card, ref)
    image['serial_number'] = 22
    mappings = browser(tmp_path, data).card(card['id'])['source_mappings']
    assert next(m for m in mappings if m['role'] != 'official_backbone')['atlas_id'] is None


def test_two_captures_of_one_platform_record_are_not_two_independent_sources(tmp_path):
    data, card, ref = source_data(tmp_path)
    external_image(tmp_path, data, card, ref)
    back = external_image(tmp_path, data, card, ref, name='back.jpg', side='back')
    back['source_artifact_sha256'] = back['identity_verification']['source_sha256'] = 'd' * 64
    index = browser(tmp_path, data)
    external = [m for m in index.card(card['id'])['source_mappings'] if m['role'] != 'official_backbone']
    assert len(external) == 1
    assert external[0]['mapping_status'] == 'needs_review'
    assert len(external[0]['observations']) == 2
    assert index.card(card['id'])['card']['atlas']['image_status'] == 'front_and_back_unpaired'
    assert index.product('product')['atlas_counts']['cross_source_confirmed_identities'] == 0


def test_read_adapter_revision_invalidates_derived_confirmation_cache(tmp_path, monkeypatch):
    import card_platform.knowledge_browser as implementation
    data, _, _ = source_data(tmp_path)
    before = browser(tmp_path, data).cache_path
    monkeypatch.setattr(implementation, 'ATLAS_READ_FINGERPRINT', 'changed-algorithm', raising=False)
    assert browser(tmp_path, data).cache_path != before
