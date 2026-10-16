"""Prepare field-scoped corrections backed by a current physical-image review."""
from copy import deepcopy
import re

from card_platform.domain.identity_review import ConfirmedIdentity


def prepare_identity_repair(current: dict, proposal: dict, source_hashes: dict,
                            *, verified_scan_sources: list[dict] | None = None) -> dict:
    """Validate a reviewed proposal; never infer unreviewed identity fields.

    The caller loads the current identity and image bytes under its own account
    and transaction boundary. Evidence is an explicit reviewer artifact, not an
    automatic OCR hypothesis or a source of market prices.
    """
    if str(current.get('collection_item_id')) != str(proposal.get('collection_item_id')):
        raise ValueError('collection item does not match the reviewed target')
    expected = proposal.get('expected_confirmed_identity_version_id')
    actual = current.get('confirmed_identity_version_id')
    if (str(actual) if actual else None) != (str(expected) if expected else None):
        raise ValueError('confirmed identity version changed after review')
    patch = proposal.get('proposed_patch') or {}
    if not patch or set(patch) - ConfirmedIdentity.model_fields.keys():
        raise ValueError('repair contains no identity fields or an unsupported field')
    before = proposal.get('before') or {}
    if set(patch) - before.keys():
        raise ValueError('every patched field requires its before value')
    for field in patch:
        if current.get(field) != before[field]:
            raise ValueError(f'identity field changed after review: {field}')
    images = proposal.get('source_images') or {}
    if not images or set(images) - {'front', 'back'}:
        raise ValueError('a reviewed physical image is required')
    for side, image in images.items():
        digest = image.get('sha256')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('invalid reviewed image hash')
        if source_hashes.get(side) != digest:
            raise ValueError(f'{side} image changed after review')
    if any(field in patch and current.get(field) is not None and patch[field] is None
           for field in ('serial_number', 'serial_total')) and set(images) != {'front', 'back'}:
        raise ValueError('clearing an unsupported serial requires both physical sides')
    observation = proposal.get('visual_observations')
    if not isinstance(observation, str) or len(observation.strip()) < 25:
        raise ValueError('specific physical observation is required')
    scans = proposal.get('full_scan_sources') or []
    verified = verified_scan_sources or []
    if not isinstance(scans, list) or len(scans) != len(verified):
        raise ValueError('original scan evidence was not verified')
    for source, checked in zip(scans, verified):
        if any(source.get(key) != checked.get(key) for key in
               ('side', 'sheet_id', 'region_id', 'sha256', 'observation')):
            raise ValueError('original scan evidence changed after verification')
    identity = ConfirmedIdentity.model_validate({
        **{key: deepcopy(value) for key, value in current.items()
           if key in ConfirmedIdentity.model_fields},
        **patch,
    }).model_dump(mode='json')
    changed = {field: value for field, value in patch.items() if value != current.get(field)}
    provenance = {
        field: {
            'source': 'codex_source_bound_field_review', 'before': before[field],
            'value': value, 'physical_observation': observation,
            'source_image_hashes': {side: image['sha256'] for side, image in images.items()},
            'official_sources': deepcopy(proposal.get('official_sources') or []),
            'reference_sources': deepcopy(proposal.get('reference_sources') or []),
            **({'full_scan_sources': deepcopy(verified)} if verified else {}),
        } for field, value in changed.items()
    }
    return {
        'identity': identity, 'field_provenance': provenance,
        'changed_fields': sorted(changed), 'review_scope': 'changed_fields_only',
        'remaining_uncertainty': proposal.get('remaining_uncertainty', ''),
    }
