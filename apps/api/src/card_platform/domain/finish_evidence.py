"""Unknown finish is a request for evidence, never an assertion of Base."""
from dataclasses import dataclass
from decimal import Decimal
import json
import re

from card_platform.domain.card_identity import CardIdentityResolver, normalize_text


_VERIFIED_FACTS = object()


def _target_key(target):
    values = target.as_dict()
    return tuple(normalize_text(values.get(field)) for field in (
        'player_name', 'season_start', 'season_end', 'brand_name', 'product_name',
        'set_name', 'card_number', 'insert', 'parallel', 'serial_number', 'serial_total',
        'print_run', 'grade_company', 'grade_value', 'is_autograph', 'is_memorabilia',
        'is_patch', 'is_redemption', 'redemption_status',
    ))


def finish_state(identity) -> str:
    values = identity if isinstance(identity, dict) else identity.as_dict()
    value = normalize_text(values.get('parallel_name') or values.get('parallel'))
    if value in {'', 'unknown', 'none', 'null', '未知', '未确认'}:
        return 'unknown'
    return 'base' if value in {'base', '普卡', '基础卡'} else 'named'


@dataclass(frozen=True)
class VisualMatchFacts:
    candidate_id: str
    title: str
    image_url: str
    raw_json: str
    _target_key: tuple = ()
    _seal: object = None
    _reviewed_proof: object = None

    def valid_for_target(self, target, title) -> bool:
        return (self._seal is _VERIFIED_FACTS and self.title == title
                and self._target_key == _target_key(target))

    def current_for(self, identity, candidate_id, title, image_url) -> bool:
        return (self._seal is _VERIFIED_FACTS and self.candidate_id == candidate_id
                and self.title == title and self.image_url == image_url
                and build_visual_match_facts(identity, candidate_id, title, image_url,
                                             json.loads(self.raw_json), reviewed_proof=self._reviewed_proof) is not None)


def build_visual_match_facts(identity, candidate_id, title, image_url, raw, *, reviewed_proof=None) -> VisualMatchFacts | None:
    """Validate current asset binding plus specific paired-front observations and independent finish proof.

    This checks the content digests captured at observation time; it does not
    claim a remote image URL is immutable. Callers must invalidate changed assets.
    """
    from card_platform.comparable_image_verifier import (
        MODEL_OBSERVATION_CONTRACT, VisualCandidate, VisualDecision,
        current_persisted_visual, normalize_model_visual_decision,
    )
    from card_platform.domain.independent_finish import independent_finish_is_current
    from card_platform.domain.reviewed_visual import ReviewedVisualProof
    if not image_url:
        return None
    if (isinstance(reviewed_proof, ReviewedVisualProof)
            and reviewed_proof.current_for(identity, candidate_id, title, image_url)):
        return VisualMatchFacts(candidate_id, title, image_url, json.dumps(raw, default=str, sort_keys=True),
            _target_key(CardIdentityResolver().resolve(identity)), _VERIFIED_FACTS, reviewed_proof)
    binding = raw.get('visual_evidence_binding') or {}
    if (finish_state(identity) == 'unknown' and not independent_finish_is_current(
            raw.get('visual_independent_confirmation'), binding)):
        return None
    if binding.get('model_observation_contract') != MODEL_OBSERVATION_CONTRACT:
        return None
    if not re.fullmatch(r'[0-9a-f]{64}', str(binding.get('candidate_sha256') or '')):
        return None
    current = current_persisted_visual(identity, VisualCandidate(candidate_id,title,image_url), raw)
    if current.get('visual_verdict') != 'same' or current.get('visual_review_required') is True:
        return None
    try:
        decision = normalize_model_visual_decision(VisualDecision(
            candidate_id=candidate_id, verdict='same',
            confidence=current.get('visual_confidence') or 0,
            design_match='same', finish_match='same',
            match_evidence=current.get('visual_match_evidence') or [],
            conflict_evidence=current.get('visual_conflict_evidence') or [],
            candidate_card_count=(1 if 'candidate_single_physical_card'
                                  in current.get('visual_reasons', []) else None),
        ))
        if decision.verdict != 'same' or decision.confidence < Decimal('.90'):
            return None
        return VisualMatchFacts(candidate_id,title,image_url,json.dumps(raw,default=str,sort_keys=True),
                                _target_key(CardIdentityResolver().resolve(identity)), _VERIFIED_FACTS)
    except (TypeError, ValueError):
        return None
