"""In-process authority minted from a current accepted database review and image bytes.

Provider payloads and serialized decisions never reconstruct this seal. Callers
supply records loaded independently for the current collection item/version.
"""
from dataclasses import dataclass
import json
import re

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.independent_finish import IndependentFinishObservation
from card_platform.valuation_trace import content_hash

REVIEWED_FINISH_CONTRACT = 'reviewed-paired-front-finish-v1'
_REVIEWED_PROOF = object()


def review_identity_hash(identity):
    return content_hash({'identity': CardIdentityResolver().resolve(identity).as_dict(),
        'supplemental_identity': {key: identity.get(key) for key in ('autograph_type', 'memorabilia_types')},
        'collection_item_id': str(identity.get('collection_item_id') or ''),
        'card_definition_id': str(identity.get('card_definition_id') or ''),
        'owner_id': str(identity.get('owner_id') or ''),
        'confirmed_identity_version_id': str(identity.get('confirmed_identity_version_id') or '')})


@dataclass(frozen=True)
class ReviewedVisualProof:
    record_id: str
    identity_sha256: str
    binding_json: str
    review_json: str
    _seal: object = None
    source_back_json: str | None = None

    def current_for(self, identity, candidate_id, title, image_url):
        from card_platform.comparable_image_verifier import VisualCandidate, visual_asset_binding
        if self._seal is not _REVIEWED_PROOF or self.identity_sha256 != review_identity_hash(identity):
            return False
        binding = json.loads(self.binding_json)
        expected = visual_asset_binding(identity, VisualCandidate(candidate_id, title, image_url), b'', None, b'')
        hashes = identity.get('image_content_hashes') or {}
        return (bool(hashes.get('front'))
                and binding.get('target_front_sha256') == hashes.get('front')
                and binding.get('target_back_sha256') == hashes.get('back')
                and (not identity.get('image_back_url') or bool(hashes.get('back')))
                and all(binding.get(key) == expected[key] for key in
                        ('contract', 'identity_sha256', 'candidate_title_sha256',
                         'source_url', 'candidate_image_url')))


def build_reviewed_visual_proof(identity, candidate, database_record, *, current_binding, current_source_back_sha256=None):
    """The record must come from the current DB review query, never raw_payload.

    current_binding must be computed from locally validated target and cached
    candidate bytes by the image verifier, rather than copied from the receipt.
    """
    from card_platform.comparable_image_verifier import (
        bound_reviewed_visual_decision, normalize_model_visual_decision,
    )
    if not isinstance(database_record, dict) or not isinstance(current_binding, dict):
        return None
    if (not database_record.get('id') or database_record.get('decision') != 'accepted'
            or not identity.get('collection_item_id') or not identity.get('confirmed_identity_version_id')
            or str(database_record.get('collection_item_id')) != str(identity['collection_item_id'])
            or str(database_record.get('identity_version_id')) != str(identity['confirmed_identity_version_id'])
            or database_record.get('source_url') != candidate.id):
        return None
    review = database_record.get('visual_review')
    if (not isinstance(review, dict) or review.get('review_contract') != REVIEWED_FINISH_CONTRACT
            or review.get('full_identity_sha256') != review_identity_hash(identity)):
        return None
    for key in ('identity_sha256', 'target_front_sha256', 'candidate_sha256', 'candidate_title_sha256'):
        if not re.fullmatch(r'[0-9a-f]{64}', str(current_binding.get(key) or '')):
            return None
    if identity.get('image_back_url') and not re.fullmatch(r'[0-9a-f]{64}', str(current_binding.get('target_back_sha256') or '')):
        return None
    decision = bound_reviewed_visual_decision(identity, candidate, review, current_binding=current_binding)
    if decision is None or decision.verdict != 'same':
        return None
    # Reuse content checks only, never model names/contracts or model authority.
    if normalize_model_visual_decision(decision).verdict != 'same':
        return None
    try:
        observation = IndependentFinishObservation.model_validate(review.get('paired_front_observation'))
        if not observation.establishes_same():
            return None
    except (TypeError, ValueError):
        return None
    from card_platform.domain.reviewed_source_back import source_back_observation_matches
    source_back = review.get('source_back_identity')
    source_back_json = None
    if (review.get('source') == 'codex_paired_image_review'
            and current_binding.get('target_back_sha256')
            and source_back_observation_matches(source_back, identity, candidate.id,
                candidate.image_url, current_source_back_sha256)):
        source_back_json = json.dumps(source_back, sort_keys=True)
    proof = ReviewedVisualProof(str(database_record['id']), review_identity_hash(identity),
        json.dumps(current_binding, sort_keys=True, default=str),
        json.dumps(review, sort_keys=True, default=str), _REVIEWED_PROOF, source_back_json)
    return proof if proof.current_for(identity, candidate.id, candidate.title, candidate.image_url) else None
