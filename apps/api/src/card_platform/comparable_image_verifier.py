from card_platform.valuation_trace import traced_completion, content_hash
import asyncio
import base64
import json
import re
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Awaitable, Callable, Literal
from urllib.parse import urlparse

import httpx
import cv2
import numpy as np
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, PrivateAttr, ValidationError

from card_platform.domain.finish_evidence import finish_state
from card_platform.domain.listing_title import explicit_print_runs
from card_platform.domain.independent_finish import (
    INDEPENDENT_FINISH_CONTRACT, IndependentFinishObservation, independent_finish_is_current,
    requires_text_only_reobservation,
)
from card_platform.config import Settings
from card_platform.visual_checkpoints import VisualCheckpoints


from card_platform.domain.listing_condition import title_discloses_minor_condition, condition_review_is_current


MODEL_OBSERVATION_CONTRACT = 'paired-image-only-evidence-v6'


class VisualSchemaError(ValueError):
    """Safe diagnostics omit Pydantic's input payload and private model text."""

    def __init__(self, stage, diagnostics, artifact):
        self.stage = stage
        self.diagnostics = diagnostics
        self.artifact = artifact
        self.safe_reasons = [
            'vision_schema_diagnostics:' + json.dumps({'stage': stage, 'errors': diagnostics}, ensure_ascii=False),
            'vision_schema_artifact:' + artifact,
        ]
        super().__init__(self.safe_reasons[0])


class VisualMatchEvidence(BaseModel):
    kind: Literal['layout', 'finish'] = Field(
        description='Positive match kind only: layout or finish. Put fixed texture observations under finish; fixed_pattern is a conflict_evidence kind, never a match_evidence kind.'
    )
    target: str
    candidate: str
    source: Literal['images', 'title', 'unknown'] = 'unknown'
    both_visible: bool = False
    target_view: Literal['front', 'back', 'unknown'] = 'unknown'
    candidate_view: Literal['front', 'back', 'unknown'] = 'unknown'


class VisualConflictEvidence(BaseModel):
    """Paired observations supporting an automated rejection, not seller inference."""

    kind: Literal[
        'layout', 'fixed_pattern', 'printed_color', 'card_text', 'serial_total',
        'serial_number', 'reflection', 'title_label',
    ]
    target: str
    candidate: str
    source: Literal['images', 'title', 'unknown'] = 'unknown'
    both_visible: bool = False


class VisualConditionReview(BaseModel):
    verdict: Literal['comparable_minor', 'materially_different', 'uncertain']
    source: Literal['images', 'title', 'unknown'] = 'unknown'
    scope: Literal['visible_front'] = 'visible_front'
    target_observation: str
    candidate_observation: str
    target_front_complete: bool = False
    candidate_front_complete: bool = False
    evidence_binding: dict | None = None


class VisualDecision(BaseModel):
    _reviewed_proof: object = PrivateAttr(default=None)
    candidate_id: str
    verdict: str = Field(pattern=r"^(same|compatible|different|uncertain)$")
    confidence: Decimal = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    design_match: str = Field(default='unknown', pattern=r'^(same|different|unknown)$')
    finish_match: str = Field(default='unknown', pattern=r'^(same|different|unknown)$')
    conflict_evidence: list[VisualConflictEvidence] = Field(default_factory=list)
    match_evidence: list[VisualMatchEvidence] = Field(default_factory=list)
    evidence_binding: dict | None = None
    independent_confirmation: dict | None = None
    condition_review: VisualConditionReview | None = None
    candidate_card_count: int | None = Field(default=None, ge=0)
    candidate_card_count_evidence: str | None = None


class VisualComparisonResult(BaseModel):
    decisions: list[VisualDecision] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class VisualCandidate:
    id: str
    title: str
    image_url: str


def enforce_visual_observations(decision: VisualDecision) -> VisualDecision:
    if 'different' in {decision.design_match, decision.finish_match}:
        return decision.model_copy(update={'verdict':'different',
            'reasons':[*decision.reasons,'explicit_design_or_finish_conflict']})
    if decision.verdict == 'same' and (decision.design_match != 'same' or decision.finish_match != 'same'):
        return decision.model_copy(update={'verdict':'uncertain',
            'reasons':[*decision.reasons,'independent_design_and_finish_observations_required']})
    return decision


def normalize_model_visual_decision(decision: VisualDecision) -> VisualDecision:
    """Require concrete paired evidence before accepting an automated conflict.

    Serial numerators, lighting and inferred parallel names cannot reject a comp.
    Invalid evidence only requests review; it never establishes a positive match.
    Explicit human/paired-image reviews retain their existing separate contract.
    """
    if decision.candidate_card_count is not None and decision.candidate_card_count > 1:
        return decision.model_copy(update={'verdict':'uncertain',
            'reasons':[*decision.reasons, 'candidate_multiple_physical_cards']})
    if decision.candidate_card_count != 1 and decision.verdict in {'same', 'compatible', 'uncertain'}:
        return decision.model_copy(update={'verdict':'uncertain',
            'reasons':[*decision.reasons, 'candidate_single_card_scope_unverified']})
    decision = enforce_visual_observations(decision)
    if decision.candidate_card_count == 1:
        decision = decision.model_copy(update={'reasons':list(dict.fromkeys([
            *decision.reasons, 'candidate_single_physical_card']))})
    if decision.verdict == 'same':
        if decision.candidate_card_count != 1:
            return decision.model_copy(update={'verdict':'uncertain',
                'reasons':[*decision.reasons, 'candidate_single_card_scope_unverified']})
        generic = {'same', 'matching', 'identical', '一致', '相同', '同款', '匹配', '一样'}
        def valid_front(e):
            return (e.source == 'images' and e.both_visible
                    and e.target_view == e.candidate_view == 'front'
                    and all(value.strip() and value.strip().casefold() not in generic
                            and not re.search(r'背面|卡背|反面|\bback(?:side)?\b', value, re.I)
                            for value in (e.target, e.candidate)))

        valid = {e.kind for e in decision.match_evidence if valid_front(e)}
        if valid != {'layout', 'finish'} or not all(map(valid_front, decision.match_evidence)):
            return decision.model_copy(update={
                'verdict': 'uncertain', 'design_match': 'unknown', 'finish_match': 'unknown',
                'reasons': [*decision.reasons, 'visual_positive_evidence_requires_review'],
            })
    if decision.verdict != 'different' and not decision.conflict_evidence:
        return decision

    conflicting_observation = False
    unsupported_conflict = False
    for evidence in decision.conflict_evidence:
        if evidence.source != 'images' or not evidence.both_visible:
            unsupported_conflict = True
            continue
        target = ' '.join(evidence.target.casefold().split())
        candidate = ' '.join(evidence.candidate.casefold().split())
        if not target or not candidate or target == candidate:
            unsupported_conflict = True
            continue
        if (evidence.kind == 'layout' and decision.design_match == 'same'
                or evidence.kind in {'fixed_pattern', 'printed_color'}
                and decision.finish_match == 'same'):
            conflicting_observation = True
            unsupported_conflict = True
            continue
        if evidence.kind == 'serial_total':
            # Require the observed fraction, so a numerator cannot accidentally
            # become the edition total. A visible denominator alone is sufficient.
            fractions = [re.fullmatch(r'([0-9]*)\s*/\s*([0-9]+)', value)
                         for value in (target, candidate)]
            if any(match is None for match in fractions):
                unsupported_conflict = True
                continue
            totals = [int(match[2]) for match in fractions]
            if any(total <= 0 or (match[1] and not 0 < int(match[1]) <= total)
                   for match, total in zip(fractions, totals, strict=True)):
                unsupported_conflict = True
                continue
            if totals[0] != totals[1]:
                conflicting_observation = True
            else:
                unsupported_conflict = True
        elif evidence.kind in {'layout', 'fixed_pattern', 'printed_color', 'card_text'}:
            conflicting_observation = True
        else:
            unsupported_conflict = True

    # Every entry claims to justify the rejection. Do not cherry-pick a plausible
    # texture claim after the model also relies on numerators, lighting or labels.
    if decision.verdict == 'different' and conflicting_observation and not unsupported_conflict:
        return decision

    if decision.verdict != 'different' and not conflicting_observation:
        return decision

    return decision.model_copy(update={
        'verdict': 'uncertain',
        # Do not leave unsupported summary conflicts able to re-promote this
        # result through the shared observation gate. Raw model output remains
        # in the completion trace and paired evidence is retained above.
        'design_match': 'unknown',
        'finish_match': 'unknown',
        'reasons': [*decision.reasons, 'visual_conflict_evidence_requires_review'],
    })


def visual_asset_binding(identity, candidate, target, target_back, candidate_content):
    fields = ('player_name','season_start','season_end','product_name','brand_name','card_number',
              'insert_name','parallel_name','serial_number','serial_total','grade_company','grade_value',
              'is_autograph','is_memorabilia','is_patch','is_redemption','redemption_status','confirmed_identity_version_id')
    return {'contract':'paired-image-observations-v1',
            'identity_sha256':content_hash({key:identity.get(key) for key in fields}),
            'target_front_sha256':content_hash(target),
            'target_back_sha256':content_hash(target_back) if target_back is not None else None,
            'candidate_sha256':content_hash(candidate_content),
            'candidate_title_sha256':content_hash(candidate.title), 'source_url':candidate.id,
            'candidate_image_url':candidate.image_url}


def has_visual_observation(raw):
    """Zero-confidence failure placeholders are not completed image observations."""
    if raw.get('visual_verdict') in {'same', 'compatible', 'different'}:
        return True
    if raw.get('visual_verdict') != 'uncertain':
        return False
    try:
        confidence = Decimal(str(raw.get('visual_confidence') or '0'))
        return confidence.is_finite() and confidence > 0
    except (ValueError, ArithmeticError):
        return False


def current_persisted_visual(identity, candidate, raw):
    """A historical same/different claim is not current evidence without its bound assets."""
    binding = raw.get('visual_evidence_binding') or {}
    target_hashes = identity.get('image_content_hashes') or {}
    expected = visual_asset_binding(identity,candidate,b'',None,b'')
    valid = (bool(target_hashes.get('front'))
        and binding.get('target_front_sha256') == target_hashes.get('front')
        and binding.get('target_back_sha256') == target_hashes.get('back')
        and (not identity.get('image_back_url') or bool(target_hashes.get('back')))
        and all(binding.get(key) == expected[key] for key in
                ('contract','identity_sha256','candidate_title_sha256','source_url','candidate_image_url'))
        and isinstance(binding.get('candidate_sha256'),str) and len(binding['candidate_sha256']) == 64)
    if (valid and raw.get('visual_verdict') == 'same' and title_discloses_minor_condition(candidate.title)
            and not condition_review_is_current(raw.get('visual_condition_review'), binding)):
        return {**raw, 'visual_verdict': 'uncertain', 'visual_review_required': True,
                'visual_reasons': [*raw.get('visual_reasons', []), 'minor_condition_requires_current_image_comparison']}
    if valid:
        explicit_review = any(reason in {
            'human_paired_image_review:version_bound_asset_review',
            'codex_paired_image_review:version_bound_asset_review',
        } for reason in raw.get('visual_reasons', []))
        accepted_contracts = {MODEL_OBSERVATION_CONTRACT}
        if (raw.get('visual_verdict') in {'same', 'different'} and not explicit_review
                and binding.get('model_observation_contract') not in accepted_contracts):
            return {**raw, 'visual_verdict': 'uncertain', 'visual_review_required': True,
                    'visual_reasons': [*raw.get('visual_reasons', []),
                                       ('historical_visual_conflict_requires_review'
                                        if raw.get('visual_verdict') == 'different'
                                        else 'historical_visual_positive_requires_review')]}
        if raw.get('visual_verdict') == 'same' and raw.get('visual_review_required') is True:
            return {**raw, 'visual_review_required':False}
        return raw
    requires_review = has_visual_observation(raw) or raw.get('visual_review_required') is True
    return {**raw, 'visual_verdict':None, 'visual_confidence':None,
            'visual_review_required':requires_review,
            'visual_reasons':['historical_visual_binding_missing_or_stale'] if requires_review
                else raw.get('visual_reasons', [])}


def bound_reviewed_visual_decision(identity: dict, candidate: VisualCandidate, review: dict, *, current_binding: dict | None = None) -> VisualDecision | None:
    """Reuse explicit paired-image adjudication only for the same image assets.

    The caller must load review rows scoped to the current item and confirmed
    identity version; text-only identity approval is never sufficient here.
    """
    if not current_binding or review.get('evidence_binding') != current_binding:
        return None
    required = ('identity_sha256','target_front_sha256','candidate_sha256','candidate_title_sha256')
    if any(not isinstance(current_binding.get(key), str) or len(current_binding[key]) != 64 for key in required):
        return None
    if review.get("source") not in {"codex_paired_image_review", "human_paired_image_review"}:
        return None
    if (not identity.get("image_front_url") or not candidate.image_url
            or review.get("target_front_url") != identity.get("image_front_url")
            or review.get("target_back_url") != identity.get("image_back_url")
            or review.get("candidate_image_url") != candidate.image_url
            or review.get("candidate_source_url") != candidate.id
            or review.get("verdict") not in {"same", "different"}
            or not review.get("reasons")):
        return None
    try:
        decision = enforce_visual_observations(VisualDecision.model_validate({**review, "candidate_id": candidate.id}))
    except ValueError:
        return None
    if decision.confidence < Decimal(".90"):
        return None
    return decision.model_copy(update={"reasons": [
        f"{review['source']}:version_bound_asset_review", *decision.reasons,
    ]})


# Legacy URL/version contract retained for purchase review. Valuation uses the
# stricter bound_reviewed_visual_decision entry point above.
def reviewed_visual_decision(identity: dict, candidate: VisualCandidate, review: dict) -> VisualDecision | None:
    """Reuse explicit paired-image adjudication only for the same image assets.

    The caller must load review rows scoped to the current item and confirmed
    identity version; text-only identity approval is never sufficient here.
    """
    if review.get("source") not in {"codex_paired_image_review", "human_paired_image_review"}:
        return None
    if (not identity.get("image_front_url") or not candidate.image_url
            or review.get("target_front_url") != identity.get("image_front_url")
            or review.get("target_back_url") != identity.get("image_back_url")
            or review.get("candidate_image_url") != candidate.image_url
            or review.get("candidate_source_url") != candidate.id
            or review.get("verdict") not in {"same", "different"}
            or not review.get("reasons")):
        return None
    try:
        decision = VisualDecision.model_validate({**review, "candidate_id": candidate.id})
    except ValueError:
        return None
    if decision.confidence < Decimal(".90"):
        return None
    return decision.model_copy(update={"reasons": [
        f"{review['source']}:version_bound_asset_review", *decision.reasons,
    ]})


class ComparableImageVerifier:
    _shared_slots = WeakKeyDictionary()
    # Vision models become materially less reliable when asked to compare one
    # reference against a large contact sheet. Keep the batch small; explicit
    # text matches/conflicts are handled independently by the classifier.
    MAX_CANDIDATES = 3
    MAX_TOTAL_CANDIDATES = 12
    MAX_IMAGE_BYTES = Settings.model_fields['max_image_bytes'].default
    MIN_TARGET_EDGE = 300

    def __init__(self, settings: Settings) -> None:
        self.MAX_IMAGE_BYTES = settings.max_image_bytes
        self.upload_dirs = (settings.resolved_upload_dir,)
        self._download_slots = asyncio.Semaphore(6)
        self.checkpoints = VisualCheckpoints(settings.resolved_upload_dir)
        self.model = settings.vision_model
        self.parallel_model = settings.vision_plus_model or settings.vision_model
        self.confirmation_model = settings.vision_finish_confirmation_model
        self._provider_key = settings.vision_base_url.rstrip('/')
        self._concurrency = settings.valuation_visual_concurrency
        self.client = (
            AsyncOpenAI(
                api_key=settings.vision_api_key,
                base_url=settings.vision_base_url,
                timeout=settings.valuation_visual_request_timeout_seconds,
                max_retries=0,
            )
            if settings.vision_api_key
            else None
        )

    def _slots(self, kind):
        providers = self._shared_slots.setdefault(asyncio.get_running_loop(), {})
        return providers.setdefault((self._provider_key, kind), asyncio.Semaphore(self._concurrency))

    @asynccontextmanager
    async def card_admission(self):
        """Wait outside the caller's bounded execution window; cancellation is safe."""
        async with self._slots('cards'):
            yield

    def _target_path(self, image_url: str | None) -> Path | None:
        if not image_url or not image_url.startswith("/uploads/"):
            return None
        filename = image_url.removeprefix("/uploads/")
        if "/" in filename:
            return None
        for upload_dir in self.upload_dirs:
            path = (upload_dir / filename).resolve()
            if path.parent == upload_dir and path.is_file():
                return path
        return None

    @staticmethod
    def _data_url(content: bytes, content_type: str) -> str:
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    @classmethod
    def _vision_data_url(cls, content: bytes, content_type: str) -> str:
        """Bound vision upload size; originals remain untouched for geometry/audit."""
        decoded = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            return cls._data_url(content, content_type)
        height, width = decoded.shape[:2]
        scale = min(1.0, 1536 / max(height, width))
        if scale < 1:
            decoded = cv2.resize(decoded, (round(width * scale), round(height * scale)),
                                 interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", decoded, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return cls._data_url(encoded.tobytes(), "image/jpeg") if ok else cls._data_url(content, content_type)

    @staticmethod
    def _geometric_template_score(
        target_image: np.ndarray, candidate_content: bytes
    ) -> tuple[int, int, Decimal]:
        candidate = cv2.imdecode(
            np.frombuffer(candidate_content, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if candidate is None:
            return 0, 0, Decimal("0")

        def scaled_gray(image: np.ndarray) -> np.ndarray:
            height, width = image.shape[:2]
            scale = min(1.0, 1600 / max(height, width))
            if scale < 1:
                image = cv2.resize(
                    image, (round(width * scale), round(height * scale)),
                    interpolation=cv2.INTER_AREA,
                )
            return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        detector = cv2.SIFT_create(nfeatures=2500)
        target_gray, candidate_gray = scaled_gray(target_image), scaled_gray(candidate)
        target_points, target_descriptors = detector.detectAndCompute(target_gray, None)
        candidate_points, candidate_descriptors = detector.detectAndCompute(candidate_gray, None)
        from card_platform.image_geometry import geometric_feature_score
        return geometric_feature_score(
            target_points, target_descriptors, target_gray.shape,
            candidate_points, candidate_descriptors, candidate_gray.shape,
        )

    def _checkpoint_request_key(self, identity, candidate, target, target_back, requires_parallel_match):
        binding = visual_asset_binding(identity, candidate, target, target_back, b'')
        scope = {key: str(identity.get(key) or '') for key in ('owner_id', 'collection_item_id')}
        return content_hash({**scope, **binding,
                             'model': self.parallel_model if requires_parallel_match else self.model,
                             'model_observation_contract': MODEL_OBSERVATION_CONTRACT,
                             'requires_parallel_match': requires_parallel_match,
                             'independent_finish_contract': INDEPENDENT_FINISH_CONTRACT if finish_state(identity) == 'unknown' else None,
                             'confirmation_model': self.confirmation_model if finish_state(identity) == 'unknown' else None})

    def order_candidates(
        self, target_image_url: str | None, candidates: list[VisualCandidate], *,
        identity: dict | None = None, target_back_image_url: str | None = None,
        requires_parallel_match: bool = False,
    ) -> list[VisualCandidate]:
        """Reuse current observations first, then advance unseen candidates.

        Completed observations only lead when their private image bytes still
        pass hash/TTL checks and bind to this exact request. Expired images and
        failed/interrupted attempts follow unseen work, even after cooldown.
        Within each checkpoint group, an unambiguous matching print-run claim
        leads unspecified candidates. This schedules observation only; no
        title claim authorizes a visual match or removes another candidate.
        No downloads occur; all other ranking ties remain stable.
        """
        if not identity or not identity.get('owner_id') or not identity.get('collection_item_id'):
            return list(candidates)
        target_path = self._target_path(target_image_url)
        try:
            if target_path is None or target_path.stat().st_size > self.MAX_IMAGE_BYTES:
                return list(candidates)
            target = target_path.read_bytes()
            back_path = self._target_path(target_back_image_url)
            target_back = (back_path.read_bytes() if back_path is not None
                           and back_path.stat().st_size <= self.MAX_IMAGE_BYTES else None)
        except OSError:
            return list(candidates)

        scope = {key: str(identity.get(key) or '') for key in ('owner_id', 'collection_item_id')}
        model = self.parallel_model if requires_parallel_match else self.model

        def priority(candidate):
            key = self._checkpoint_request_key(identity, candidate, target, target_back,
                                               requires_parallel_match)
            record = self.checkpoints.read('decisions', key)
            if not record:
                return 1
            try:
                stored = VisualDecision.model_validate(record['decision'])
                if stored.verdict == 'uncertain' and requires_text_only_reobservation(stored.independent_confirmation):
                    return 2
                if stored.candidate_id == candidate.id and stored.confidence > 0 and stored.evidence_binding:
                    image_key = content_hash({**scope, 'image_url': candidate.image_url})
                    image = self.checkpoints.image(image_key, self.MAX_IMAGE_BYTES)
                    if image is not None:
                        binding = visual_asset_binding(identity, candidate, target, target_back, image[0])
                        expected = {**binding, 'model': model,
                                    'model_observation_contract': MODEL_OBSERVATION_CONTRACT}
                        if stored.evidence_binding == expected:
                            return 0
            except (KeyError, TypeError, ValueError):
                pass
            # Reservations survive cancellation and retain their attempted
            # status independently of the retry window's attempt count.
            return 2

        try:
            target_total = int(identity.get('serial_total') or identity.get('print_run') or 0)
        except (TypeError, ValueError):
            target_total = 0
        return sorted(candidates, key=lambda candidate: (
            priority(candidate),
            not (target_total > 0 and explicit_print_runs(candidate.title) == {target_total}),
        ))

    def _reviewed_asset_decision(self, identity, candidate, review, binding):
        from card_platform.domain.reviewed_visual import build_reviewed_visual_proof
        record = review.get('database_record')
        source_back_sha = None
        observation = ((record or {}).get('visual_review') or {}).get('source_back_identity')
        if isinstance(observation, dict) and observation.get('image_url'):
            key = content_hash({key: str(identity.get(key) or '') for key in ('owner_id', 'collection_item_id')}
                               | {'image_url': observation['image_url']})
            cached = self.checkpoints.image(key, self.MAX_IMAGE_BYTES)
            if cached is not None:
                try:
                    if cv2.imdecode(np.frombuffer(cached[0], dtype=np.uint8), cv2.IMREAD_COLOR) is not None:
                        source_back_sha = content_hash(cached[0])
                except cv2.error:
                    pass
        proof = build_reviewed_visual_proof(identity, candidate, record, current_binding=binding,
                                           current_source_back_sha256=source_back_sha)
        if proof is not None:
            decision = bound_reviewed_visual_decision(identity, candidate,
                record['visual_review'], current_binding=binding)
            decision._reviewed_proof = proof
            return decision
        # Existing explicit rejection reviews never create positive finish facts.
        if review.get('decision') == 'rejected':
            return bound_reviewed_visual_decision(identity, candidate,
                review.get('review') or {}, current_binding=binding)
        return None

    def reviewed_decision_from_cached_assets(self, identity, candidate, database_record):
        """Re-mint authority only after reading current local/cached image bytes."""
        if not isinstance(database_record, dict):
            return None
        front = self._target_path(identity.get('image_front_url'))
        back = self._target_path(identity.get('image_back_url'))
        if front is None or (identity.get('image_back_url') and back is None):
            return None
        try:
            if any(p.stat().st_size > self.MAX_IMAGE_BYTES for p in (front, back) if p):
                return None
            target, target_back = front.read_bytes(), back.read_bytes() if back else None
        except OSError:
            return None
        scope = {key: str(identity.get(key) or '') for key in ('owner_id', 'collection_item_id')}
        if not all(scope.values()):
            return None
        cached = self.checkpoints.image(content_hash({**scope, 'image_url': candidate.image_url}), self.MAX_IMAGE_BYTES)
        if cached is None:
            return None
        try:
            valid_images = all(content and cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR) is not None
                               for content in (target, target_back, cached[0]) if content is not None)
        except cv2.error:
            return None
        if not valid_images:
            return None
        binding = visual_asset_binding(identity, candidate, target, target_back, cached[0])
        return self._reviewed_asset_decision(identity, candidate,
            {'database_record': database_record}, binding)

    async def verify(
        self,
        target_image_url: str | None,
        candidates: list[VisualCandidate],
        *,
        requires_parallel_match: bool = False,
        target_back_image_url: str | None = None,
        reviewed_assets: dict | None = None,
        identity: dict | None = None,
        on_decision: Callable[[VisualDecision], Awaitable[None]] | None = None,
        should_stop: Callable[[VisualDecision], bool] | None = None,
    ) -> dict[str, VisualDecision]:
        target_path = self._target_path(target_image_url)
        if target_path is None or not candidates:
            return {}
        if self.client is None:
            # Cached, independently loaded reviews need no model or network.
            decisions = {}
            for candidate in candidates[:self.MAX_TOTAL_CANDIDATES]:
                record = ((reviewed_assets or {}).get(candidate.id) or {}).get('database_record')
                decision = self.reviewed_decision_from_cached_assets(identity or {}, candidate, record)
                if decision is not None:
                    decisions[candidate.id] = decision
                    if on_decision is not None:
                        await on_decision(decision)
            return decisions
        target = target_path.read_bytes()
        if len(target) > self.MAX_IMAGE_BYTES:
            return {}
        target_image = cv2.imread(str(target_path))
        if target_image is None:
            return {}
        low_resolution_target = min(target_image.shape[:2]) < self.MIN_TARGET_EDGE
        target_type = "image/png" if target_path.suffix.casefold() == ".png" else "image/jpeg"
        back_path = self._target_path(target_back_image_url)
        target_back = None
        if back_path is not None and back_path.stat().st_size <= self.MAX_IMAGE_BYTES:
            target_back = (
                back_path.read_bytes(),
                "image/png" if back_path.suffix.casefold() == ".png" else "image/jpeg",
            )
        identity = identity or {'image_front_url': target_image_url, 'image_back_url': target_back_image_url}
        requested = candidates[: self.MAX_TOTAL_CANDIDATES]
        decisions: dict[str, VisualDecision] = {}
        scope = {key: str(identity.get(key) or '') for key in ('owner_id', 'collection_item_id')}
        checkpoint_enabled = all(scope.values())
        model = self.parallel_model if requires_parallel_match else self.model

        def request_key(candidate):
            return self._checkpoint_request_key(identity, candidate, target,
                                                target_back[0] if target_back else None,
                                                requires_parallel_match)

        async def publish(decision, key, state, binding=None, geometry=None, *, persist=True):
            if binding:
                decision = decision.model_copy(update={'evidence_binding': {
                    **(decision.evidence_binding or {}), **binding,
                }})
            if geometry:
                good, inliers, ratio = geometry
                if good >= 20 and inliers >= 15 and ratio >= Decimal('0.25'):
                    reason = f'geometry_same_template:matches={good},inliers={inliers},ratio={ratio:.3f}'
                    decision = decision.model_copy(update={'reasons': list(dict.fromkeys([*decision.reasons, reason]))})
            if checkpoint_enabled and persist:
                self.checkpoints.write('decisions', key, {**state, 'decision': decision.model_dump(mode='json')})
            decisions[decision.candidate_id] = decision
            if on_decision is not None:
                await on_decision(decision)

        def failure(candidate, reason):
            return VisualDecision(candidate_id=candidate.id, verdict='uncertain',
                                  confidence=Decimal('0'), reasons=[reason])

        headers = {'User-Agent': 'Mozilla/5.0 CardPlatform/1.0'}
        # Reject redirects, rather than trusting a permitted URL to redirect into
        # private networks or arbitrary third-party hosts.
        async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=False) as client:
            async def prepare(candidate):
                key = request_key(candidate)
                record = self.checkpoints.read('decisions', key) if checkpoint_enabled else {}
                review = (reviewed_assets or {}).get(candidate.id, {})
                stored = None
                try:
                    stored = VisualDecision.model_validate(record['decision'])
                    if stored.candidate_id != candidate.id:
                        stored = None
                except (KeyError, TypeError, ValueError):
                    pass
                completed = stored is not None and stored.confidence > 0 and bool(stored.evidence_binding)
                if (completed and stored.verdict == 'same' and title_discloses_minor_condition(candidate.title)
                        and not condition_review_is_current(stored.condition_review.model_dump(mode='json')
                            if stored.condition_review else None, stored.evidence_binding)):
                    completed = False
                if completed and stored.verdict == 'uncertain' and requires_text_only_reobservation(stored.independent_confirmation):
                    completed = False
                try:
                    parsed = urlparse(candidate.image_url)
                    permitted = (parsed.scheme == 'https' and parsed.hostname == 'images.cardhobby.com'
                                 and parsed.port in (None, 443) and parsed.username is None and parsed.password is None)
                except ValueError:
                    permitted = False
                image_key = content_hash({**scope, 'image_url': candidate.image_url})
                downloaded = self.checkpoints.image(image_key, self.MAX_IMAGE_BYTES) if checkpoint_enabled and permitted else None
                # Only a review bound to validated, current image bytes can
                # supersede cooldown. A text-only/stale review row must not
                # authorize another download or model attempt.
                if downloaded is not None and review:
                    binding = visual_asset_binding(identity, candidate, target,
                                                   target_back[0] if target_back else None, downloaded[0])
                    manual = self._reviewed_asset_decision(identity, candidate, review, binding)
                    expected = {'accepted': 'same', 'rejected': 'different'}.get(review.get('decision'))
                    if (manual and manual.verdict == expected and (manual.verdict != 'same'
                        or not title_discloses_minor_condition(candidate.title)
                        or condition_review_is_current(manual.condition_review.model_dump(mode='json')
                            if manual.condition_review else None, binding))):
                        await publish(manual, key, record, binding, persist=False)
                        return None
                if (checkpoint_enabled and not completed
                        and self.checkpoints.retry_state(record)[2]):
                    decision = failure(candidate, 'visual_retry_deferred')
                    await publish(decision, key, record, persist=False)
                    return None
                state = record
                if downloaded is None and permitted:
                    for attempt in range(2):
                        try:
                            async with self._download_slots:
                                response = await client.get(candidate.image_url)
                            response.raise_for_status()
                            content_type = response.headers.get('content-type', '').split(';', 1)[0]
                            content = response.content
                            if (content_type not in {'image/jpeg', 'image/png', 'image/webp'}
                                    or not content or len(content) > self.MAX_IMAGE_BYTES
                                    or cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR) is None):
                                break
                            downloaded = content, content_type
                            if checkpoint_enabled:
                                self.checkpoints.save_image(image_key, content, content_type)
                            break
                        except httpx.HTTPError:
                            if attempt == 0:
                                await asyncio.sleep(.1)
                if downloaded is None:
                    if checkpoint_enabled:
                        state = self.checkpoints.reserve(key, record)
                    await publish(failure(candidate, 'candidate_image_download_failed'), key, state)
                    return None
                content, content_type = downloaded
                binding = visual_asset_binding(identity, candidate, target,
                                               target_back[0] if target_back else None, content)
                manual = self._reviewed_asset_decision(identity, candidate, review, binding)
                expected = {'accepted': 'same', 'rejected': 'different'}.get(review.get('decision'))
                if (manual and manual.verdict == expected and (manual.verdict != 'same'
                        or not title_discloses_minor_condition(candidate.title)
                        or condition_review_is_current(manual.condition_review.model_dump(mode='json')
                            if manual.condition_review else None, binding))):
                    # Manual reviews remain authoritative in their own store.
                    await publish(manual, key, state, binding, persist=False)
                    return None
                expected_binding = {**binding, 'model_observation_contract': MODEL_OBSERVATION_CONTRACT, 'model': model}
                if (completed and stored.evidence_binding == expected_binding
                        and (finish_state(identity) != 'unknown' or stored.verdict != 'same'
                             or independent_finish_is_current(stored.independent_confirmation, expected_binding))):
                    await publish(stored.model_copy(update={'reasons': [*stored.reasons, 'visual_checkpoint_reused']}),
                                  key, state, persist=False)
                    return None
                geometry = await asyncio.to_thread(self._geometric_template_score, target_image, content)
                return (candidate, content, content_type), key, state, expected_binding, geometry

            async def compare_prepared(prepared):
                if prepared is None:
                    return
                row, key, record, binding, geometry = prepared
                candidate = row[0]
                async with self._slots('models'):
                    # Waiting for provider capacity is not an inference attempt.
                    state = self.checkpoints.reserve(key, record) if checkpoint_enabled else {}
                    try:
                        result = await self._compare_batch(target, target_type, [row],
                            low_resolution_target=low_resolution_target, target_back=target_back,
                            requires_parallel_match=requires_parallel_match)
                    except Exception as error:
                        from card_platform.domain.model_failures import model_failure_reason
                        failed = failure(candidate, model_failure_reason(error, 'same_card'))
                        if isinstance(error, VisualSchemaError):
                            failed = failed.model_copy(update={'reasons': [*failed.reasons, *error.safe_reasons]})
                        result = {candidate.id: failed}
                    decision = result.get(candidate.id) or failure(candidate, 'vision_model_omitted_candidate')
                    if (finish_state(identity) == 'unknown' and decision.verdict == 'same'
                            and decision.confidence >= Decimal('.90')):
                        try:
                            confirmation = await self._confirm_unknown_finish(target, target_type, row, binding)
                            decision = decision.model_copy(update={'independent_confirmation': confirmation})
                            if not independent_finish_is_current(confirmation, binding):
                                decision = decision.model_copy(update={
                                    'verdict': 'uncertain', 'design_match': 'unknown', 'finish_match': 'unknown',
                                    'reasons': [*decision.reasons, 'independent_finish_confirmation_requires_review']})
                        except Exception as error:
                            from card_platform.domain.model_failures import model_failure_reason
                            decision = decision.model_copy(update={
                                'verdict': 'uncertain', 'confidence': Decimal('0'),
                                'design_match': 'unknown', 'finish_match': 'unknown',
                                'independent_confirmation': None,
                                'reasons': [*decision.reasons, model_failure_reason(error, 'independent_finish'),
                                            *(error.safe_reasons if isinstance(error, VisualSchemaError) else [])]})
                    if decision.condition_review is not None:
                        decision = decision.model_copy(update={'condition_review': decision.condition_review.model_copy(
                            update={'evidence_binding': binding})})
                    await publish(decision, key, state, binding, geometry)

            # Prepare locally in parallel, but spend one model attempt at a time
            # per card. A slow trailing download cannot block an earlier result.
            preparations = [asyncio.create_task(prepare(candidate)) for candidate in requested]
            try:
                for candidate, prepared in zip(requested, preparations):
                    await compare_prepared(await prepared)
                    decision = decisions.get(candidate.id)
                    if decision is not None and should_stop is not None and should_stop(decision):
                        break
            finally:
                for prepared in preparations:
                    if not prepared.done():
                        prepared.cancel()
                await asyncio.gather(*preparations, return_exceptions=True)
        return decisions

    def _parse_visual_response(self, response_text, schema_type, stage):
        try:
            return schema_type.model_validate_json(response_text)
        except ValidationError as error:
            diagnostics = [{key: row[key] for key in ('type', 'loc', 'msg')}
                           for row in error.errors(include_input=False, include_url=False)[:20]]
            response_hash = content_hash(response_text)
            # Retain only bounded schema text, never echoed images/base64. The
            # private atomic checkpoint writer creates mode600 files outside uploads.
            omitted = (len(response_text.encode()) > 128 * 1024
                       or bool(re.search(r'data:[^;\s]+;base64,|[A-Za-z0-9+/]{1024,}={0,2}', response_text)))
            key = content_hash({'stage': stage, 'response_sha256': response_hash})
            self.checkpoints.write('schema-errors', key, {
                'stage': stage, 'response_sha256': response_hash,
                'response_text': None if omitted else response_text,
                'response_omitted': omitted, 'diagnostics': diagnostics,
            })
            artifact = str(self.checkpoints.root / 'schema-errors' / f'{key}.json')
            raise VisualSchemaError(stage, diagnostics, artifact) from None

    async def _confirm_unknown_finish(self, target, target_type, row, binding):
        """Blind second context: front images only, no identity, title or prior claim."""
        if not self.confirmation_model:
            raise ValueError('independent_finish_model_not_configured')
        _, candidate_content, candidate_type = row
        schema = json.dumps(IndependentFinishObservation.model_json_schema(), ensure_ascii=False)
        prompt = '请独立核验两张卡的正面是否同一版式且同一固定表面版本。第一张为候选，第二张为目标。没有标题、没有背面、没有上一轮判断；不要猜官方平行名、卡号、限编或看不见的信息。先分别只描述候选和目标各自可见的外框固定颜色、物理纹理及所在区域、版式。每个描述必须能单独成立，不能写“同上”“同目标”“一样”或把另一张的描述复制过来。人物照片与排版一样不代表表面版本一样。比较全卡外框与图像区域固定纹理，包括网纹、点纹、几何线纹、格纹、碎片；并区分固定红/粉/蓝外框和银箔在光照下的彩虹。记录仅由拍摄产生的反射另作lighting_or_reflection，不把所有颜色差异都当反光；看不清则unknown。先校正拍摄角度再比版式，不以透视小差异拒绝。只有双方正面版式、固定印刷颜色和纹理结构都有具体观察且一致才same。任一固定颜色或纹理存在明确差异则different。所有单张观察须仅据各自图片，不据人物或产品常识补全。输出简洁JSON。'
        prompt += ('先逐个观察每张卡上的全部主体，按从左到右、从上到下填写artwork_subjects：'
                   '每个人物/角色的躯干和头部朝向、手臂弯曲与手的位置、球或物体相对身体的位置，以及具体选图细节。'
                   '没有球或手臂时明确描述没有，遮挡看不清则visible=false，不能据球员姓名补姿势。'
                   '然后逐主体核对是否同一张照片或同一幅插画，填写artwork_match。相同球员、球衣、双人排版、边框、logo或文字都不能证明选图相同。'
                   '持球位置、手臂/躯干姿态或照片内容有实质不同，即使版式与纹理完全相同也必须artwork_match=different且verdict=different。'
                   '只有全部主体照片/插画、版式、固定颜色及纹理均同且具体可见才same；任一看不清必须unknown/uncertain。')
        prompt += ('严格区分人物图稿与无人物的纯文字兑换凭条：普通人物/角色卡artwork_kind=figurative，仍必须逐主体填写上述姿态，'
                   '不能借text_only跳过人物。仅整张正面清晰可见、确无人物或角色插画的兑换凭条才能artwork_kind=text_only，'
                   'artwork_subjects为空并填写text_only_artwork；空主体本身绝不构成same。'
                   'text_only_artwork.contract=text-only-print-v1，document_kind=redemption_notice，'
                   '分别据各自正面确认whole_front_visible及no_figurative_artwork，具体描述printed_graphics。'
                   '逐字段抄录object_name（承诺对象）、product、card_type（承诺卡种）、card_code（公共卡号）、year、expiry_date，'
                   '每个字段填写status、value、location和source=images；任一不可读或未印均uncertain，不用标题或另一张图补齐。'
                   'parallel_and_serial也必须逐张读；只有整张正面确认没有平行或限编文字才not_printed且value=null；'
                   '双方均明确未印或实际印字一致才能同款。同模板但对象/产品/卡种/公共卡号/年份/到期/平行限编任一不同则different。'
                   '不要输出兑换码；card_code只指HM-JG之类公共卡号，绝非背面私人兑换码。'
                   '正面文字相同不能证明未刮、未使用或仍可兑换，兑换状态保持独立约束，不推断兑换状态；'
                   '文字图稿仍须版式、固定颜色、纹理和artwork_match全部有具体双图正证。')
        response = await traced_completion(self.client.chat.completions.create,
            stage='independent_finish', model=self.confirmation_model,
            messages=[{'role': 'system', 'content': f'只输出符合 JSON Schema 的 JSON：{schema}'},
                      {'role': 'user', 'content': [
                          {'type': 'text', 'text': prompt},
                          {'type': 'text', 'text': '候选正面，图片1'},
                          {'type': 'image_url', 'image_url': {'url': self._vision_data_url(candidate_content, candidate_type)}},
                          {'type': 'text', 'text': '目标正面，图片2'},
                          {'type': 'image_url', 'image_url': {'url': self._vision_data_url(target, target_type)}},
                      ]}],
            response_format={'type': 'json_object'}, extra_body={'enable_thinking': False}, temperature=0)
        observation = self._parse_visual_response(
            response.choices[0].message.content or '', IndependentFinishObservation, 'independent_finish')
        return {'contract': INDEPENDENT_FINISH_CONTRACT, 'model': self.confirmation_model,
                'evidence_binding': dict(binding), 'observation': observation.model_dump(mode='json')}

    async def _compare_batch(
        self,
        target: bytes,
        target_type: str,
        downloaded: list[tuple[VisualCandidate, bytes, str]],
        *,
        low_resolution_target: bool,
        target_back: tuple[bytes, str] | None = None,
        requires_parallel_match: bool = False,
    ) -> dict[str, VisualDecision]:
        primary_schema = VisualComparisonResult.model_json_schema()
        primary_schema['$defs']['VisualDecision']['properties'].pop('independent_confirmation', None)
        schema = json.dumps(primary_schema, ensure_ascii=False)
        content: list[dict] = [
            {
                "type": "text",
                "text": (
                    "第一张图片是目标卡。后续图片按候选编号排列。逐张判断候选是否与目标"
                    "属于同一球员、同一产品、同一正式卡型/insert、同一平行层级和同一"
                    "限编总数。交换板/Redemption Card 与实际兑换后的实体卡不是同一形态。"
                    "若提供目标背面，必须用背面的卡号、产品发行标记、限编与评级交叉核验。"
                    "球员生涯统计年份不是发行年份；版权年份不能自动等同发行赛季。"
                    "目标正背面是同一张卡，不能把背面当成候选。"
                    "每张候选只附一张商品图片；没有候选背面时，不能声称看到了候选背面"
                    "卡号或限编，也不能将目标背面的信息复制给候选。卖家标题只是线索。"
                    "交换板常共用模板，必须读清球员、承诺卡种、卡号和平行文字，"
                    "不能凭相同底图判 same；文字不清时返回 uncertain。不要输出兑换码。"
                    "先分别找出卡片四角，按卡片自身宽高把关键点坐标归一化，"
                    "不要用整张照片的坐标。位置比较必须先校正拍摄透视与长宽比例，"
                    "不能把卡片倾斜、扫描与实拍"
                    "的透视差别当成人物姿态变化。"
                    "必须逐项比较：球衣号码中心点的横纵坐标、号码"
                    "整体倾斜角度、号码是否发生透视拉伸、人物头肩和双手姿态、人物在卡面"
                    "左右位置、Logo、卡名和奖项文字位置。尤其不能把同系列中号码位置或"
                    "人物姿态不同的两张卡判为同卡。请在 reasons 中明确写出观察到的号码"
                    "位置（例如中间偏右/左下）、倾斜方向和姿态差异。流水号不同允许；"
                    "图片裁切、拍摄角度、亮度不同允许。重点比较卡面模板、物料窗数量与"
                    "形状、签字区、Logo、边框和文字位置。明显为大窗与"
                    "三窗、不同 insert、不同平行或不同限编时 verdict=different。仅因图片"
                    "模糊无法判断时 verdict=uncertain，不能猜 same。"
                    "不同颜色/折射纹理的平行版不能以'正常变体'或'同为基础RC'判same。"
                    "区分固定油墨/纹理与镭射反光：同一银箔在不同角度可呈红橙、绿或彩虹，"
                    "不能仅凭整张反光颜色不同断定红折/金折；先看球员照片是否黑白、"
                    "固定文字颜色、纹理结构、Logo及实卡可见限编。无法区分则uncertain。"
                    "same=可见证据支持同款同平行；compatible=同系列但平行未确认；"
                    "different=有明确相冲突的卡型/固定颜色/纹理/姿态等；"
                    "uncertain=图片或证据不足。compatible不是同款成交确认。"
                    "必须分别填写design_match与finish_match。先独立描述目标和候选的构图与固定表面纹理，再比较。"
                    "先填写candidate_card_count和candidate_card_count_evidence：候选图实际包含几张不同实卡，"
                    "同一卡正背并列只算1张，套卡/两张正面算2张以上，卖家条款图算0；遮挡或无法判断填null。"
                    "有多张不同实卡时，即使其中一张与目标完全相同也不能给单卡same，必须描述分别可见的卡面。"
                    "正证match_evidence.kind只能为layout或finish，不得填写fixed_pattern、printed_color等冲突枚举。"
                    "fixed_pattern仅属于conflict_evidence.kind；相同固定纹理的正证应写kind=finish，具体纹样放target/candidate描述。"
                    "若判same，必须填写match_evidence至少两项kind=layout和finish，分别写出target与candidate"
                    "具体可见的正面布局、固定印刷/纹理观察，不能只写相同或匹配；source=images、both_visible=true、"
                    "target_view与candidate_view均为front。目标背面只用于目标身份核对，不能作为候选的正证据，"
                    "不能声称双方背面卡号/限编一致；不能从标题补全看不清的候选纹理。缺少任一正面观察则uncertain。"
                    "大星形、细颗粒、方格、碎冰、波纹是不同纹理，不能因相同照片或限编就填same。"
                    "黑白/负片印刷与彩色照片不同；正常彩虹反光并不改变印刷照片颜色。"
                    "看不清任一对象的纹理就填unknown。两项均same才允许verdict=same。"
                    "另行填写condition_review：独立观察双方正面的四角、边缘和表面。"
                    "记录target_observation与candidate_observation具体可见的磨损、白边、折痕等及可见范围；"
                    "不能从标题或同款结论推断品相一致。双方正面四角及实体边缘完整清晰才标front_complete=true。"
                    "均无明显结构损伤且可见品相无实质差异、仅轻微瑕疵可比时verdict=comparable_minor，"
                    "有实质差异为materially_different，看不清/被遮挡/裁边为uncertain。source=images，"
                    "scope=visible_front；只描述实际可见正面，不能声称未见背面或保证评级/完美品相。"
                    "condition_review不改变卡型verdict，evidence_binding留空由服务绑定原图。"
                    "若判different或任一match为different，必须填写conflict_evidence，"
                    "每项分别写target和candidate的直接观察，source=images仅用于实际图片证据，"
                    "双方均清晰可见才填both_visible=true。kind区分layout（布局）、"
                    "fixed_pattern（固定纹理结构）、printed_color（固定印刷颜色）、"
                    "card_text（实际可读的身份文字）、serial_total（限编分母）、"
                    "serial_number（流水号分子）、reflection（拍摄反光）、title_label（标题推断）。"
                    "serial_total须记录实见完整分数如47/50，或仅可读分母/50；47/50与35/50"
                    "分母相同不能作为冲突。反光颜色变化只能归reflection，不能归fixed_pattern"
                    "或printed_color。仅凭卖家标题或缺少平行名文字不能断定冲突；"
                    "insert名称不等于平行名，例如Shadow Etch不能仅凭名称当作平行差异。"
                    "card_text仅比较实际可读且相冲突的身份文字，不包含流水号或标题标签。"
                    "没有清晰的布局、固定纹理/印刷、身份文字或限编分母冲突则返回uncertain。"
                    "每条候选只写1-2条简短具体理由，合计不超过100字，不要重复推理。"
                    + (
                        "本次必须确认平行版本；相同照片/排版不足以证明同平行。"
                        "缩略图看不清折射纹理或限编分母时返回uncertain。"
                        if requires_parallel_match else ""
                    )
                    + (
                        "目标图分辨率较低：仍需利用可见的号码位置和人物轮廓做软判断，"
                        "但不得仅凭模糊纹理给出高置信度。"
                        if low_resolution_target
                        else ""
                    )
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": self._vision_data_url(target, target_type)},
            },
        ]
        if target_back is not None:
            content.extend([
                {"type": "text", "text": "目标卡背面（非候选）"},
                {"type": "image_url", "image_url": {
                    "url": self._vision_data_url(target_back[0], target_back[1]),
                }},
            ])
        labels: dict[str, VisualCandidate] = {}
        for index, (candidate, image, content_type) in enumerate(downloaded, start=1):
            label = f"candidate_{index}"
            labels[label] = candidate
            content.extend(
                [
                    {
                        "type": "text",
                        # Titles are checked by the identity pipeline. Supplying
                        # them here biases purported image-only observations.
                        "text": f"候选编号 {label}",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": self._vision_data_url(image, content_type)},
                    },
                ]
            )
        response = await traced_completion(self.client.chat.completions.create, stage='same_card',
            model=self.parallel_model if requires_parallel_match else self.model,
            messages=[
                {
                    "role": "system",
                    "content": f"只输出符合 JSON Schema 的 JSON：{schema}",
                },
                {"role": "user", "content": content},
            ],
            response_format={"type": "json_object"},
            extra_body={"enable_thinking": False},
        )
        result = response.choices[0].message.content
        if not result:
            return {}
        parsed = self._parse_visual_response(result, VisualComparisonResult, 'same_card')
        return {
            labels[decision.candidate_id].id: decision.model_copy(
                update={
                    "candidate_id": labels[decision.candidate_id].id,
                    "independent_confirmation": None,
                    "confidence": min(decision.confidence, Decimal("0.79")),
                    "evidence_binding": {
                        "model_observation_contract": MODEL_OBSERVATION_CONTRACT,
                    },
                }
            )
            if low_resolution_target
            else decision.model_copy(
                update={"candidate_id": labels[decision.candidate_id].id,
                        "independent_confirmation": None,
                        "evidence_binding": {
                            "model_observation_contract": MODEL_OBSERVATION_CONTRACT,
                        }}
            )
            for raw_decision in parsed.decisions
            for decision in [normalize_model_visual_decision(raw_decision)]
            if decision.candidate_id in labels
        }
