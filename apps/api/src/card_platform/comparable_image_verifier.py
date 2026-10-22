from card_platform.model_requests import nonthinking_extra_body
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

from card_platform.domain.listing_title import explicit_print_runs
from card_platform.domain.independent_finish import (
    INDEPENDENT_FINISH_CONTRACT, IndependentFinishObservation,
    requires_text_only_reobservation,
)
from card_platform.config import Settings
from card_platform.visual_checkpoints import VisualCheckpoints


from card_platform.domain.listing_condition import title_discloses_minor_condition, condition_review_is_current


MODEL_OBSERVATION_CONTRACT = 'paired-image-similar-finish-v10'


PRIMARY_VISUAL_OBSERVATION_INSTRUCTIONS = """核验图中两张实卡是否同一正式卡型、同一选图、同一固定印刷/纹理版本。只据图片，不用标题或产品常识补字。按图像编号辨认：target_front为目标正面，target_back为目标背面；candidate_front为候选主图，candidate_support_1/2为候选画廊，可能是背面、局部或卖家条款，角色必须实看。条款不是卡背。目标背面永远不是候选。没有候选背面时，不能声称看到了候选背面卡号或限编。已编号上传的候选补充背图是该候选的图证；即使与目标背面版式相同，也不能因此误当成目标背面的重复引用而说未提供候选背面。
交换板/Redemption Card与兑现后的实体卡不是同一形态；须核承诺对象、公共卡号和卡种，禁止输出私人兑换码。物料窗数量/形状及签字区必须逐方核对。生涯统计年不是发行年，版权年不能自动等同发行赛季。先校正透视、倾斜与裁切再比较人物布局，不凭整张照片的拍摄坐标判姿势冲突。

先观察、后比较、最后下结论。正面与背面有不同职责，禁止混写。
1. 独立观察两张正面。对每张分别写完整且具体的布局、人物照片/插画姿势与手/物体位置、框线/文字位置、固定表面纹理及所在区域。candidate描述必须单独可读；禁用同上、同左、同目标、与目标一致、same as target等指代。只写这张图实际可见内容。
2. 独立读实际提供的两张背面。核卡号的完整字母前缀及数字、产品发行标记、背面语言/地区印刷、限编分母与版本印字。将实读背号/语言的对照简短写在reasons，不能写进正面match_evidence描述。同号不保证同语言版本。完整字母前缀不可丢失；连字符/空格可以是标点变化，不能把前缀不同的编号合并。未提供或看不清的卡背不能用另一张卡补全；兑换码禁止抄录。流水分子不同本身不属于异版，分母必须核对。
3. 区分固定油墨/纹理与镭射反光。银箔/全息面随角度产生红橙、蓝紫、绿黄和彩虹渐变；先比同一区域的纹理形状、网格尺度、线条方向，以及不反光的文字/照片/油墨细节。将实际光照差异描述为reflection，不能只把虹彩渐变或曝光导致的色相改变标为printed_color/fixed_pattern。固定蓝框、金色油墨、星形/波纹/碎冰/Scope等明确差异仍是异版；未出现彩虹反射不等于没有全息层；低亮度或不同拍摄角度不能单独证明目标是哑光底材、候选是异版。要判固定工艺不同，须实见纹理结构、固定油墨或印字的冲突。不确定属于哪类就unknown，不能默认都是反光。黑白与彩色选图、人物姿势、背号或语言印刷差异不能被相同模板掩盖。
4. 最后判断：只有单张实卡、全部可见身份/选图/固定颜色与纹理证据相容，且花纹相似、固定颜色无明显差异、总限编相同或双方无编号即允许same；不必核实官方平行名称或取得背面版本印字，轻微反光色差允许；有可读背号、语言、选图或固定工艺冲突则different；证据不足uncertain；compatible不是同款成交确认。

输出遵守schema，并按以下职责填写：
- candidate_card_count：同一卡正背算1；多张实卡/lot大于1，条款0；不清楚null。
- match_evidence：same时恰好两项kind=layout和finish。target、candidate均是各自独立、具体的【正面】观察，source=images，both_visible=true，target_view/candidate_view=front。这四个描述字符串不得包含“背面/卡背/反面/back”或任何背面事实；背面事实只放reasons和version_marker_observations。
- finish.version_marker_observations：如实际可见则逐方读PRIZM/REFRACTOR等版本印字。有target_back时target必须view=back、image_ref=target_back；candidate必须引用实际看见的面：candidate_front或candidate_support_1/2。readable要逐字抄实际印字；完整清晰整面未印才not_printed且whole_view_visible=true，没提供/看不清只能not_visible。版本印字专指工艺/平行标签，不是卡号、流水、球衣号、产品Logo或版权文字；没有版本标签就据整面可见性标not_printed，不能把相同卡号冒充相同版本字。产品Logo不能代替背面版本印字。not_visible表示对应面未提供或看不清；not_printed表示整面已清晰可见且确实未印版本字。已提供清晰候选背面但没有版本标签，应为not_printed，不应因没字填not_visible。一方readable而另一方not_printed也属于版本冲突，不能判same。双方对应面均not_printed时，版本字这一项允许相容；它不是缺图，但也不单独证明同款，仍须双方正面固定纹理/颜色/选图证据。只有实际印字相冲突，才属于明确冲突；缺少版本标签或背图不单独阻止正面图片相似匹配。
- conflict_evidence：different时每项分别写两张图清晰可见且互相冲突的观察，source=images、both_visible=true。纹理结构差异用fixed_pattern，确切固定油墨用printed_color，实读背号/语言用card_text，分母用serial_total（如12/99对7/25）。单独光照/分子/标题不构成异版证据。
- condition_review：只核双方正面可见四角/实体边缘/表面，分别具体描述；清晰完整才front_complete=true。微瑕可比comparable_minor，实质不同materially_different，不清楚uncertain。不能从同款结论推出品相。
每张只有一个candidate_id。请保持所有单张描述独立，先写观察字段再填写design_match、finish_match、verdict和confidence。不得用相同照片模板代替真实图证。"""


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


class VersionMarkerObservation(BaseModel):
    status: Literal['readable', 'not_visible', 'not_printed']
    view: Literal['front', 'back', 'unknown']
    text: str | None = None
    source: Literal['images', 'unknown'] = 'unknown'
    whole_view_visible: bool = False
    image_ref: str = 'unknown'


class VersionMarkerPair(BaseModel):
    target: VersionMarkerObservation
    candidate: VersionMarkerObservation

    def establishes_match(self, binding: dict) -> bool:
        pair = (self.target, self.candidate)
        if any(p.source != 'images' or p.view == 'unknown' for p in pair):
            return False
        expected_target = 'target_back' if binding.get('target_back_sha256') else 'target_front'
        if (self.target.image_ref != expected_target
                or self.target.view != expected_target.split('_')[1]
                or not re.fullmatch(r'[0-9a-f]{64}', str(binding.get(expected_target + '_sha256') or ''))):
            return False
        if self.candidate.view == 'front':
            if (self.candidate.image_ref != 'candidate_front'
                    or not re.fullmatch(r'[0-9a-f]{64}', str(binding.get('candidate_sha256') or ''))):
                return False
        else:
            # A self-reported back observation cannot invent an uploaded image.
            match = re.fullmatch(r'candidate_support_([12])', self.candidate.image_ref)
            supports = binding.get('candidate_supporting_images') or []
            if not isinstance(supports,list) or len(supports)>2 or not match or len(supports) < int(match[1]):
                return False
            support = supports[int(match[1])-1]
            if not isinstance(support,dict) or not re.fullmatch(r'[0-9a-f]{64}', str(support.get('sha256') or '')):
                return False
        if all(p.status == 'readable' for p in pair):
            texts = [' '.join((p.text or '').split()).casefold() for p in pair]
            return bool(texts[0] and texts[0] == texts[1] and self.target.view == self.candidate.view
                        and texts[0] not in {'same','unknown','none','not_printed','一致','未知','同上'})
        return (all(p.status == 'not_printed' and p.whole_view_visible and not p.text for p in pair)
                and self.target.view == self.candidate.view)


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
    version_marker_observations: VersionMarkerPair | None = Field(default=None,
        description='Optional supporting finish evidence. Independently read printed version/finish labels (e.g. REFRACTOR/PRIZM) on each actual card, including any supplied back. Never copy the target label onto an unseen candidate back. not_printed requires the entire observed side visible; not_visible means unknown. This is separate from the required paired FRONT layout/texture descriptions.')


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
    supporting_image_urls: tuple[str, ...] = ()


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
                            and not re.search(r'同上|同目标|同候选|(?:与|與)(?:目标|目標|候选|候選)(?:完全)?(?:一致|相同)|\b(?:same as|identical to|matches) (?:the )?(?:target|candidate)\b', value, re.I)
                            for value in (e.target, e.candidate)))

        valid = {e.kind for e in decision.match_evidence if valid_front(e)}
        if valid != {'layout', 'finish'} or not all(map(valid_front, decision.match_evidence)):
            return decision.model_copy(update={
                'verdict': 'uncertain', 'design_match': 'unknown', 'finish_match': 'unknown',
                'reasons': [*decision.reasons, 'visual_positive_evidence_requires_review'],
            })
        # Missing finish labels do not block similar pictured versions. An
        # actually readable contradiction still excludes the candidate.
        for evidence in decision.match_evidence:
            pair = evidence.version_marker_observations
            if evidence.kind != 'finish' or pair is None:
                continue
            a, b = pair.target, pair.candidate
            if a.source == b.source == 'images' and (
                a.status == b.status == 'readable'
                and ' '.join((a.text or '').split()).casefold()
                    != ' '.join((b.text or '').split()).casefold()
                or {a.status, b.status} == {'readable', 'not_printed'}
                and (a.whole_view_visible if a.status == 'not_printed' else b.whole_view_visible)
            ):
                return decision.model_copy(update={'verdict': 'different',
                    'reasons': [*decision.reasons, 'explicit_printed_version_conflict']})
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


def visual_asset_binding(identity, candidate, target, target_back, candidate_content, candidate_support=()):
    fields = ('player_name','season_start','season_end','product_name','brand_name','card_number',
              'insert_name','parallel_name','serial_number','serial_total','grade_company','grade_value',
              'is_autograph','is_memorabilia','is_patch','is_redemption','redemption_status','confirmed_identity_version_id')
    return {'contract':'paired-image-observations-v1',
            'identity_sha256':content_hash({key:identity.get(key) for key in fields}),
            'target_front_sha256':content_hash(target),
            'target_back_sha256':content_hash(target_back) if target_back is not None else None,
            'candidate_sha256':content_hash(candidate_content),
            'candidate_title_sha256':content_hash(candidate.title), 'source_url':candidate.id,
            'candidate_image_url':candidate.image_url,
            'candidate_supporting_images': [{'url':url, 'sha256':content_hash(content)}
                                             for url, content, _ in candidate_support]}


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
                             'supporting_image_urls': candidate.supporting_image_urls,
                             'model': self.parallel_model if requires_parallel_match else self.model,
                             'model_observation_contract': MODEL_OBSERVATION_CONTRACT,
                             'requires_parallel_match': requires_parallel_match})

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
                        supports = []
                        for support_url in list(dict.fromkeys(candidate.supporting_image_urls))[:2]:
                            if support_url == candidate.image_url:
                                continue
                            cached = self.checkpoints.image(content_hash({**scope,'image_url':support_url}),self.MAX_IMAGE_BYTES)
                            if cached is None:
                                return 2
                            supports.append((support_url,*cached))
                        binding = visual_asset_binding(identity,candidate,target,target_back,image[0],supports)
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
        recorded_support = (((record or {}).get('visual_review') or {}).get('evidence_binding') or {}).get('candidate_supporting_images')
        if recorded_support and not binding.get('candidate_supporting_images'):
            if not isinstance(recorded_support,list) or len(recorded_support) > 2:
                return None
            verified_support = []
            for image in recorded_support:
                url = image.get('url') if isinstance(image,dict) else None
                try:
                    parsed = urlparse(url or '')
                    if not (parsed.scheme == 'https' and parsed.hostname == 'images.cardhobby.com'
                            and parsed.port in (None,443) and not parsed.username and not parsed.password):
                        return None
                    key = content_hash({key:str(identity.get(key) or '') for key in ('owner_id','collection_item_id')}
                                       | {'image_url':url})
                    cached = self.checkpoints.image(key,self.MAX_IMAGE_BYTES)
                    if cached is None or cv2.imdecode(np.frombuffer(cached[0],dtype=np.uint8),cv2.IMREAD_COLOR) is None:
                        return None
                    verified_support.append({'url':url,'sha256':content_hash(cached[0])})
                except (ValueError,cv2.error):
                    return None
            binding = {**binding,'candidate_supporting_images':verified_support}
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
                            if manual.condition_review else None, manual.evidence_binding))):
                        await publish(manual, key, record, manual.evidence_binding, persist=False)
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
                supports = []
                for support_url in list(dict.fromkeys(candidate.supporting_image_urls))[:2]:
                    if support_url == candidate.image_url:
                        continue
                    try:
                        parsed_support = urlparse(support_url)
                        if not (parsed_support.scheme == 'https' and parsed_support.hostname == 'images.cardhobby.com'
                                and parsed_support.port in (None,443) and not parsed_support.username
                                and not parsed_support.password):
                            continue
                        support_key = content_hash({**scope, 'image_url': support_url})
                        support = self.checkpoints.image(support_key, self.MAX_IMAGE_BYTES) if checkpoint_enabled else None
                        if support is None:
                            async with self._download_slots:
                                response = await client.get(support_url)
                            response.raise_for_status()
                            support_type = response.headers.get('content-type','').split(';',1)[0]
                            support_content = response.content
                            if (support_type not in {'image/jpeg','image/png','image/webp'}
                                    or not support_content or len(support_content) > self.MAX_IMAGE_BYTES
                                    or cv2.imdecode(np.frombuffer(support_content,dtype=np.uint8),cv2.IMREAD_COLOR) is None):
                                continue
                            support = support_content, support_type
                            if checkpoint_enabled:
                                self.checkpoints.save_image(support_key,*support)
                        supports.append((support_url,*support))
                    except (httpx.HTTPError,ValueError):
                        continue
                binding = visual_asset_binding(identity, candidate, target,
                                               target_back[0] if target_back else None, content, supports)
                manual = self._reviewed_asset_decision(identity, candidate, review, binding)
                expected = {'accepted': 'same', 'rejected': 'different'}.get(review.get('decision'))
                if (manual and manual.verdict == expected and (manual.verdict != 'same'
                        or not title_discloses_minor_condition(candidate.title)
                        or condition_review_is_current(manual.condition_review.model_dump(mode='json')
                            if manual.condition_review else None, manual.evidence_binding))):
                    # Manual reviews remain authoritative in their own store.
                    await publish(manual, key, state, manual.evidence_binding, persist=False)
                    return None
                expected_binding = {**binding, 'model_observation_contract': MODEL_OBSERVATION_CONTRACT, 'model': model}
                if completed and stored.evidence_binding == expected_binding:
                    await publish(stored.model_copy(update={'reasons': [*stored.reasons, 'visual_checkpoint_reused']}),
                                  key, state, persist=False)
                    return None
                geometry = await asyncio.to_thread(self._geometric_template_score, target_image, content)
                return (candidate, content, content_type), key, state, expected_binding, geometry, supports

            async def compare_prepared(prepared):
                if prepared is None:
                    return
                row, key, record, binding, geometry, supports = prepared
                candidate = row[0]
                async with self._slots('models'):
                    # Waiting for provider capacity is not an inference attempt.
                    state = self.checkpoints.reserve(key, record) if checkpoint_enabled else {}
                    try:
                        result = await self._compare_batch(target, target_type, [row],
                            low_resolution_target=low_resolution_target, target_back=target_back,
                            candidate_support={candidate.id:supports},
                            requires_parallel_match=requires_parallel_match)
                    except Exception as error:
                        from card_platform.domain.model_failures import model_failure_reason
                        failed = failure(candidate, model_failure_reason(error, 'same_card'))
                        if isinstance(error, VisualSchemaError):
                            failed = failed.model_copy(update={'reasons': [*failed.reasons, *error.safe_reasons]})
                        result = {candidate.id: failed}
                    decision = result.get(candidate.id) or failure(candidate, 'vision_model_omitted_candidate')
                    # One concrete comparison of the current paired images is
                    # sufficient; an unknown catalog finish needs no second vote.
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
        prompt += ('判断固定颜色前，先分别定位每张图可见的金属箔或涂层反射区域，再对齐卡面对应区域。'
                   '具体观察颜色变化的边界：是沿固定油墨图形或固定纹理边界，还是渐变、高光连续跨过人物、文字与底纹。'
                   '跨图形的光带、明暗渐变、金属表面随拍摄显现或未显现的虹彩，只记入lighting_or_reflection，'
                   '不要先把照片中的金/橙/棕等表观色命名为固定油墨，再用该命名证明版本冲突。'
                   'outer_frame_fixed_color只描述各自图中有可见油墨边界或稳定印刷区域支持的颜色；'
                   '不能猜被反光遮住的颜色，也不能因一张没有显现虹彩便说没有该涂层。'
                   '只有双方对应区域清晰，且可见油墨边界、稳定印色或纹理拓扑证明实质差异，才据此different。'
                   '无法区分固定色与光照时必须unknown/uncertain；这不构成same，仍须逐项取得正面版式、选图、固定印色与纹理的正证。')
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
                          {'type': 'text', 'text': '候选正面，图片1 image_ref=candidate_front'},
                          {'type': 'image_url', 'image_url': {'url': self._vision_data_url(candidate_content, candidate_type)}},
                          {'type': 'text', 'text': '目标正面，图片2 image_ref=target_front'},
                          {'type': 'image_url', 'image_url': {'url': self._vision_data_url(target, target_type)}},
                      ]}],
            response_format={'type': 'json_object'}, extra_body=nonthinking_extra_body(self.confirmation_model), temperature=0)
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
        candidate_support: dict | None = None,
    ) -> dict[str, VisualDecision]:
        primary_schema = VisualComparisonResult.model_json_schema()
        primary_schema['$defs']['VisualDecision']['properties'].pop('independent_confirmation', None)
        if target_back is not None:
            marker_schema = primary_schema['$defs']['VersionMarkerObservation']
            primary_schema['$defs']['TargetVersionMarkerObservation'] = {
                **marker_schema, 'properties': {**marker_schema['properties'],
                    'view': {'type':'string','const':'back'},
                    'image_ref': {'type':'string','const':'target_back'}},
                'required': list(dict.fromkeys([*marker_schema.get('required',[]),'image_ref']))}
            primary_schema['$defs']['VersionMarkerPair']['properties']['target'] = {
                '$ref':'#/$defs/TargetVersionMarkerObservation'}
        schema = json.dumps(primary_schema, ensure_ascii=False)
        content: list[dict] = [
            {
                "type": "text",
                "text": (
                    PRIMARY_VISUAL_OBSERVATION_INSTRUCTIONS
                    + (
                        "本次按花纹相似、固定颜色无明显差异及相同总限编或双方无编号判断同款，不要求官方平行名称完全一致。"
                        "大类花纹可比较即可，无需逐点核验；图像完全无法比较时返回uncertain。"
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
            {"type": "text", "text": "目标正面 image_ref=target_front"},
            {
                "type": "image_url",
                "image_url": {"url": self._vision_data_url(target, target_type)},
            },
        ]
        if target_back is not None:
            content.extend([
                {"type": "text", "text": "目标卡背面（非候选） image_ref=target_back"},
                {"type": "image_url", "image_url": {
                    "url": self._vision_data_url(target_back[0], target_back[1]),
                }},
            ])
        labels: dict[str, VisualCandidate] = {}
        image_bindings = {}
        for index, (candidate, image, content_type) in enumerate(downloaded, start=1):
            label = f"candidate_{index}"
            labels[label] = candidate
            supports = (candidate_support or {}).get(candidate.id, [])[:2]
            image_bindings[label] = {**visual_asset_binding({},candidate,target,
                target_back[0] if target_back else None,image,supports),
                'model_observation_contract':MODEL_OBSERVATION_CONTRACT}
            content.extend(
                [
                    {
                        "type": "text",
                        # Titles are checked by the identity pipeline. Supplying
                        # them here biases purported image-only observations.
                        "text": f"候选编号 {label} image_ref=candidate_front",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": self._vision_data_url(image, content_type)},
                    },
                ]
            )
            for index, (_, support_bytes, support_type) in enumerate(supports,1):
                content.extend([{'type':'text','text':f'候选编号 {label} 补充图片 image_ref=candidate_support_{index}（角色需实看，不预设卡背）'},
                    {'type':'image_url','image_url':{'url':self._vision_data_url(support_bytes,support_type)}}])
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
            extra_body=nonthinking_extra_body(self.parallel_model if requires_parallel_match else self.model), temperature=0,
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
                    "evidence_binding": image_bindings[decision.candidate_id],
                }
            )
            if low_resolution_target
            else decision.model_copy(
                update={"candidate_id": labels[decision.candidate_id].id,
                        "independent_confirmation": None,
                        "evidence_binding": image_bindings[decision.candidate_id]}
            )
            for raw_decision in parsed.decisions
            for decision in [normalize_model_visual_decision(raw_decision.model_copy(
                update={'evidence_binding':image_bindings.get(raw_decision.candidate_id,{})}))]
            if decision.candidate_id in labels
        }
