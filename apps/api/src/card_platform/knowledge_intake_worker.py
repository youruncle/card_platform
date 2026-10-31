"""Cloud-catalog work planning and audited candidate export; never a local KB."""
from collections import deque
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re

from card_platform.knowledge_intake_sources import content_hash


def plan_targets(products, artifact_root, *, limit=300, years=(2023, 2024, 2025)):
    if not 1 <= limit <= 5000:
        raise ValueError('pilot_limit_must_be_between_1_and_5000')
    root = Path(artifact_root).resolve()
    buckets, records, missing, product_count = deque(), 0, 0, 0
    for product in sorted(products, key=lambda p: (
            -(p.get('attributes', {}).get('season_start') or 0), p['canonical_name'])):
        attrs = product.get('attributes', {})
        artifact = attrs.get('official_artifact')
        if not artifact or attrs.get('season_start') not in years:
            continue
        product_count += 1
        path = (root / artifact['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('artifact_path_outside_root')
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != artifact['checksum']:
            raise ValueError('official_artifact_checksum_mismatch')
        entities = json.loads(gzip.decompress(raw))
        if len(entities) != artifact['card_count']:
            raise ValueError('official_artifact_count_mismatch')
        records += len(entities)
        known = {}
        for ref in attrs.get('reference_images', []):
            verification = ref.get('identity_verification') or {}
            if (verification.get('status') == 'verified'
                    and verification.get('scope') != 'official_section'):
                known.setdefault(verification.get('card_id'), set()).add(ref.get('side'))
        selected, seen = [], set()
        entities.sort(key=lambda e: (
            bool(e['attributes'].get('parallel')), bool(e['attributes'].get('serial_total')),
            bool(e['attributes'].get('insert')), str(e['attributes'].get('card_number')),
            str(e['attributes'].get('player'))))
        for entity in entities:
            row = entity['attributes']
            sides = [side for side in ['front', 'back'] if side not in known.get(entity['id'], set())]
            if not sides or not row.get('player') or not row.get('card_number'):
                continue
            identity = {name: row.get(source) for name, source in {
                'player_name': 'player', 'brand_name': 'brand', 'product_name': 'product',
                'season_start': 'season_start', 'season_end': 'season_end',
                'card_number': 'card_number', 'insert_name': 'insert', 'parallel_name': 'parallel',
                'serial_total': 'serial_total', 'sport': 'sport',
            }.items()}
            identity.update(section=row.get('section'), source_key=artifact['source_key'])
            key = content_hash(identity)
            if key in seen:
                continue
            seen.add(key)
            missing += 1
            if len(selected) < limit:
                selected.append(dict(target_hash=key, card_id=entity['id'], identity=identity,
                                     missing_sides=sides, product_title=product['canonical_name'],
                                     official_evidence={
                                         **{k: row.get(k) for k in ['source_url', 'source_checksum',
                                                                   'source_locator']},
                                         'artifact': artifact,
                                         'artifact_path': str(path),
                                     }))
        if selected:
            buckets.append(deque(selected))
    targets = []
    while buckets and len(targets) < limit:
        bucket = buckets.popleft()
        targets.append(bucket.popleft())
        if bucket:
            buckets.append(bucket)
    return dict(targets=targets, product_count=product_count, catalog_records=records,
                missing_version_targets=missing, selected_targets=len(targets),
                selection_policy='product_round_robin_then_unnumbered_before_numbered',
                year_basis='season_start', years=list(years))


def checked_file(root, reference):
    root = Path(root).resolve()
    path = (root / reference['path']).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('capture_path_outside_batch_or_missing')
    if sha256(path.read_bytes()).hexdigest() != reference['sha256']:
        raise ValueError('capture_checksum_changed_after_review')
    return path


def validate_review(candidate, review, root):
    from card_platform.knowledge_media import validate_image
    payload = candidate['payload']
    actual_hash = content_hash({k: v for k, v in payload.items() if k != 'content_sha256'})
    if (actual_hash != candidate['content_sha256']
            or review.get('candidate_sha256') != actual_hash):
        raise ValueError('review_candidate_checksum_mismatch')
    if (review.get('decision') not in {'accepted', 'rejected'}
            or not str(review.get('reviewer') or '').strip()
            or not str(review.get('reason') or '').strip()):
        raise ValueError('review_requires_decision_reviewer_reason')
    if review['decision'] == 'rejected':
        return
    target = candidate['target']
    if review.get('identity') != target['identity']:
        raise ValueError('review_identity_differs_from_frozen_catalog_target')
    evidence = review.get('identity_evidence') or {}
    if not all(isinstance(evidence.get(k), str) and evidence[k].strip() for k in
               ['player_name', 'card_number', 'product_name', 'season_start']):
        raise ValueError('review_requires_visible_identity_evidence')
    fields = ['insert_name', 'parallel_name', 'serial_total']
    section = re.sub(r'[^a-z0-9]', '', str(target['identity'].get('section') or '').casefold())
    if section and section not in {'base', 'baseset', 'basecards', 'basecard'}:
        fields.append('section')
    for field in fields:
        if target['identity'].get(field) not in (None, ''):
            if not isinstance(evidence.get(field), str) or not evidence[field].strip():
                raise ValueError('review_requires_visible_version_evidence:' + field)
    total = target['identity'].get('serial_total')
    if total is not None:
        serials = [(int(a), int(b)) for a, b in
                   re.findall(r'(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)', evidence['serial_total'])]
        if len(serials) != 1 or not 0 < serials[0][0] <= serials[0][1] == int(total):
            raise ValueError('visible_serial_conflicts_with_official_version')
    if review.get('pair_id') or review.get('pair_evidence'):
        raise ValueError('v1_unpaired_images_cannot_claim_physical_pair')
    checked_file(root, payload['source_capture'])
    if payload.get('search_capture'):
        checked_file(root, payload['search_capture'])
    images = payload['images']
    decisions = review.get('images') or []
    if (len(decisions) != len(images)
            or {d.get('index') for d in decisions} != set(range(len(images)))):
        raise ValueError('every_candidate_image_requires_a_review')
    accepted, sides_by_hash = [], {}
    for decision in decisions:
        image = images[decision['index']]
        if (decision.get('sha256') != image['sha256']
                or decision.get('decision') not in {'accepted', 'rejected'}
                or not str(decision.get('observations') or '').strip()):
            raise ValueError('image_review_is_not_bound_to_captured_bytes')
        data = checked_file(root, image).read_bytes()
        validate_image(data)
        if decision['decision'] == 'accepted':
            side = decision.get('side')
            if side not in {'front', 'back'}:
                raise ValueError('accepted_image_needs_observed_front_or_back')
            previous = sides_by_hash.setdefault(image['sha256'], side)
            if previous != side:
                raise ValueError('identical_image_cannot_be_both_faces')
            accepted.append(decision)
    if not accepted:
        raise ValueError('accepted_candidate_has_no_accepted_card_image')


def _store_json(root, value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str).encode()
    digest = sha256(raw).hexdigest()
    path = Path(root) / 'captures' / (digest + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError('immutable_export_capture_collision')
    path.write_bytes(raw)
    return path, digest


def export_assets(candidates, root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    assets, seen = [], {}
    for candidate in candidates:
        if candidate.get('decision') != 'accepted' or not candidate.get('review'):
            raise ValueError('unreviewed_candidate_cannot_be_exported')
        review, payload, target = candidate['review'], candidate['payload'], candidate['target']
        validate_review(candidate, review, root)
        source = payload['source_capture']
        source_path, source_sha = _store_json(root, dict(
            contract='intake-public-image-source-v1', page_url=payload['source_url'],
            record_id=payload['item_id'], html_path=Path(source['path']).name,
            html_sha256=source['sha256'],
            primary_image_url=payload['source_gallery']['primary_image_url'],
            observed_link_bindings=[{'url': image['url']} for image in payload['images']],
        ))
        review_path, review_sha = _store_json(root, dict(
            contract='intake-public-image-review-v1', candidate=payload, target=target, review=review))
        identity = target['identity']
        artifact = target['official_evidence']['artifact']
        artifact_path = Path(target['official_evidence']['artifact_path'])
        if sha256(artifact_path.read_bytes()).hexdigest() != artifact['checksum']:
            raise ValueError('official_artifact_checksum_mismatch')
        for decision in review['images']:
            if decision['decision'] != 'accepted':
                continue
            image = payload['images'][decision['index']]
            key = (identity['source_key'], image['sha256'])
            if key in seen:
                if seen[key] != (target['card_id'], decision['side']):
                    raise ValueError('duplicate_image_has_conflicting_card_binding')
                continue
            seen[key] = (target['card_id'], decision['side'])
            assets.append(dict(
                asset_id='intake-v1:' + content_hash(key)[:24],
                path=str(checked_file(root, image)), sha256=image['sha256'],
                source_url=payload['source_url'], image_url=image['url'],
                source_key=identity['source_key'], title=target['product_title'],
                publisher=identity['brand_name'], season_start=identity['season_start'],
                season_end=identity['season_end'], player=identity['player_name'],
                card_number=identity['card_number'], variant=identity['parallel_name'],
                side=decision['side'], image_type='verified_card_scan',
                caption=f"{target['product_title']}; {identity['player_name']} #{identity['card_number']}; "
                        f"{decision['side']}; {decision['observations']}",
                status='ready', visibility='public_reference',
                captured_at=source['captured_at'], reviewed_at=datetime.now(timezone.utc).isoformat(),
                width=image['width'], height=image['height'], format=image['format'],
                source_artifact_path=str(source_path), source_artifact_sha256=source_sha,
                review_artifact_path=str(review_path), review_artifact_sha256=review_sha,
                source_authority='public_specimen_listing', locator=image['url'],
                review_basis=review['reason'], pair_id=None,
                usage_scope='internal_reference_research',
                rights='Original source rights retained; source attribution preserved.',
                source_card_identity=review['identity'],
                identity_verification=dict(
                    scope='reviewed_unpaired_public_image', status='verified',
                    card_id=target['card_id'], candidate_card_ids=[target['card_id']],
                    artifact_path=str(artifact_path), artifact_sha256=artifact['checksum'],
                    source_url=payload['source_url'], source_sha256=source_sha,
                    source_locator=f"public item {payload['item_id']}; reviewed image {decision['index']}",
                ),
            ))
    output.mkdir(parents=True, exist_ok=True)
    data = ''.join(json.dumps(a, ensure_ascii=False) + '\n' for a in assets)
    temporary = output / 'assets.jsonl.tmp'
    temporary.write_text(data)
    temporary.replace(output / 'assets.jsonl')
    return dict(images=len(assets), assets_sha256=sha256(data.encode()).hexdigest(),
                physical_pairs=0, knowledge_published=False)


def verify_unpaired_export(asset, official_row):
    """Replay raw gallery and review binding at the existing publication boundary."""
    from card_platform.ingestion.cardhobby_public_item import (
        parse_public_item_html, public_gallery_for_primary,
    )
    if asset.get('pair_id') or asset.get('pair_evidence'):
        raise ValueError('unpaired_public_image_cannot_claim_pair')
    review_path = Path(asset['review_artifact_path'])
    if sha256(review_path.read_bytes()).hexdigest() != asset['review_artifact_sha256']:
        raise ValueError('public_image_review_checksum_mismatch')
    package = json.loads(review_path.read_bytes())
    if package.get('contract') != 'intake-public-image-review-v1':
        raise ValueError('unknown_public_image_review_contract')
    payload, target, review = package['candidate'], package['target'], package['review']
    candidate = dict(payload=payload, target=target, content_sha256=payload['content_sha256'])
    root = Path(asset['source_artifact_path']).resolve().parent.parent
    validate_review(candidate, review, root)
    if (review['decision'] != 'accepted' or target['card_id'] != asset['identity_verification']['card_id']
            or target['identity']['source_key'] != asset['source_key']
            or target['identity'].get('parallel_name') != asset.get('variant')
            or asset['source_url'] != payload['source_url']
            or not any(image['sha256'] == asset['sha256'] and image['url'] == asset['image_url']
                       and decision['side'] == asset['side'] and decision['decision'] == 'accepted'
                       for decision in review['images']
                       for image in [payload['images'][decision['index']]])):
        raise ValueError('public_image_review_does_not_bind_this_card_face')
    for name, field in {'brand_name': 'brand', 'product_name': 'product',
                        'season_start': 'season_start', 'season_end': 'season_end',
                        'player_name': 'player', 'card_number': 'card_number',
                        'insert_name': 'insert', 'parallel_name': 'parallel',
                        'serial_total': 'serial_total', 'section': 'section'}.items():
        if target['identity'].get(name) != official_row.get(field):
            raise ValueError('public_image_identity_disagrees_with_official_row')
    wrapper = json.loads(Path(asset['source_artifact_path']).read_bytes())
    if (wrapper.get('contract') != 'intake-public-image-source-v1'
            or wrapper.get('page_url') != payload['source_url']
            or wrapper.get('record_id') != payload['item_id']
            or wrapper.get('html_sha256') != payload['source_capture']['sha256']):
        raise ValueError('public_image_source_wrapper_mismatch')
    source_root = Path(asset['source_artifact_path']).resolve().parent
    raw = checked_file(source_root, dict(path=wrapper['html_path'], sha256=wrapper['html_sha256']))
    parsed = parse_public_item_html(raw.read_text(), payload['source_url'])
    gallery = public_gallery_for_primary(parsed, payload['source_url'], wrapper['primary_image_url'])
    if asset['image_url'] not in gallery.get('image_urls', []):
        raise ValueError('public_image_not_bound_to_original_gallery')
    return {k: v for k, v in asset['identity_verification'].items() if k != 'artifact_path'}
