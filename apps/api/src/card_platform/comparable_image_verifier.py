from card_platform.valuation_trace import traced_completion, content_hash
import asyncio
import base64
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import httpx
import cv2
import numpy as np
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from card_platform.config import Settings


class VisualDecision(BaseModel):
    candidate_id: str
    verdict: str = Field(pattern=r"^(same|compatible|different|uncertain)$")
    confidence: Decimal = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    design_match: str = Field(default='unknown', pattern=r'^(same|different|unknown)$')
    finish_match: str = Field(default='unknown', pattern=r'^(same|different|unknown)$')
    evidence_binding: dict | None = None


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
    if valid:
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
    # Vision models become materially less reliable when asked to compare one
    # reference against a large contact sheet. Keep the batch small; explicit
    # text matches/conflicts are handled independently by the classifier.
    MAX_CANDIDATES = 3
    MAX_TOTAL_CANDIDATES = 12
    MAX_IMAGE_BYTES = 8 * 1024 * 1024
    MIN_TARGET_EDGE = 300

    def __init__(self, settings: Settings) -> None:
        self.upload_dirs = (settings.resolved_upload_dir,)
        self._download_slots = asyncio.Semaphore(6)
        self.model = settings.vision_model
        self.parallel_model = settings.vision_plus_model or settings.vision_model
        self.client = (
            AsyncOpenAI(
                api_key=settings.vision_api_key,
                base_url=settings.vision_base_url,
                timeout=settings.vision_timeout_seconds,
                max_retries=1,
            )
            if settings.vision_api_key
            else None
        )

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
        target_points, target_descriptors = detector.detectAndCompute(
            scaled_gray(target_image), None
        )
        candidate_points, candidate_descriptors = detector.detectAndCompute(
            scaled_gray(candidate), None
        )
        if target_descriptors is None or candidate_descriptors is None:
            return 0, 0, Decimal("0")
        pairs = cv2.BFMatcher().knnMatch(
            target_descriptors, candidate_descriptors, k=2
        )
        good = [match for match, other in pairs if match.distance < 0.72 * other.distance]
        if len(good) < 8:
            return len(good), 0, Decimal("0")
        source = np.float32(
            [target_points[match.queryIdx].pt for match in good]
        ).reshape(-1, 1, 2)
        destination = np.float32(
            [candidate_points[match.trainIdx].pt for match in good]
        ).reshape(-1, 1, 2)
        _, mask = cv2.findHomography(source, destination, cv2.RANSAC, 5.0)
        if mask is None:
            return len(good), 0, Decimal("0")
        inliers = int(mask.sum())
        return len(good), inliers, Decimal(str(inliers / len(good)))

    async def verify(
        self,
        target_image_url: str | None,
        candidates: list[VisualCandidate],
        *,
        requires_parallel_match: bool = False,
        target_back_image_url: str | None = None,
        reviewed_assets: dict | None = None,
        identity: dict | None = None,
    ) -> dict[str, VisualDecision]:
        target_path = self._target_path(target_image_url)
        if self.client is None or target_path is None or not candidates:
            return {}
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
        downloaded: list[tuple[VisualCandidate, bytes, str]] = []
        geometry: dict[str, tuple[int, int, Decimal]] = {}
        requested = candidates[: self.MAX_TOTAL_CANDIDATES]
        headers = {"User-Agent": "Mozilla/5.0 CardPlatform/1.0"}
        async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as client:
            async def download(candidate: VisualCandidate):
                parsed = urlparse(candidate.image_url)
                if parsed.scheme != "https" or parsed.hostname != "images.cardhobby.com":
                    return None
                try:
                    async with self._download_slots:
                        response = await client.get(candidate.image_url)
                    response.raise_for_status()
                except httpx.HTTPError:
                    return None
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if (
                    content_type not in {"image/jpeg", "image/png", "image/webp"}
                    or len(response.content) > self.MAX_IMAGE_BYTES
                ):
                    return None
                return candidate, response.content, content_type

            results = await asyncio.gather(*(download(candidate) for candidate in requested))
            downloaded = [result for result in results if result is not None]
            scores = await asyncio.gather(*(
                asyncio.to_thread(
                    self._geometric_template_score, target_image, candidate_content
                )
                for _candidate, candidate_content, _content_type in downloaded
            ))
            geometry.update({
                candidate.id: score
                for (candidate, _content, _content_type), score in zip(
                    downloaded, scores, strict=True
                )
            })
        decisions: dict[str, VisualDecision] = {}
        downloaded_ids = {candidate.id for candidate, _, _ in downloaded}
        for candidate in requested:
            if candidate.id not in downloaded_ids:
                decisions[candidate.id] = VisualDecision(
                    candidate_id=candidate.id,
                    verdict="uncertain",
                    confidence=Decimal("0"),
                    reasons=["candidate_image_download_failed"],
                )
        if not downloaded:
            return decisions
        # ORB/RANSAC is only a supporting signal.  Shared player photos, card
        # sleeves, scanner backgrounds and repeated product templates can all
        # produce many inliers for a different insert or base card.  Every
        # downloaded candidate therefore still goes through semantic vision.
        identity = identity or {'image_front_url':target_image_url,'image_back_url':target_back_image_url}
        bindings = {}
        for candidate, candidate_content, _ in downloaded:
            binding = visual_asset_binding(identity, candidate, target,
                                           target_back[0] if target_back else None, candidate_content)
            bindings[candidate.id] = binding
            cached = (reviewed_assets or {}).get(candidate.id, {})
            decision = bound_reviewed_visual_decision(identity, candidate, cached.get('review') or {}, current_binding=binding)
            expected = {'accepted':'same','rejected':'different'}.get(cached.get('decision'))
            if decision and decision.verdict == expected:
                decisions[candidate.id] = decision
        model_candidates = [row for row in downloaded if row[0].id not in decisions]
        # Finish comparisons are fine-grained: a multi-candidate contact sheet
        # caused attributes/backs to leak between candidates. Compare pairs.
        batch_size = 1 if requires_parallel_match else self.MAX_CANDIDATES
        batches = [
            model_candidates[index : index + batch_size]
            for index in range(0, len(model_candidates), batch_size)
        ]
        if not batches:
            return decisions
        results = await asyncio.gather(
            *(
                self._compare_batch(
                    target,
                    target_type,
                    batch,
                    low_resolution_target=low_resolution_target,
                    target_back=target_back,
                    requires_parallel_match=requires_parallel_match,
                )
                for batch in batches
            ),
            return_exceptions=True,
        )
        for batch, result in zip(batches, results, strict=True):
            if isinstance(result, Exception):
                for candidate, _, _ in batch:
                    decisions[candidate.id] = VisualDecision(
                        candidate_id=candidate.id,
                        verdict="uncertain",
                        confidence=Decimal("0"),
                        reasons=[f"vision_batch_failed:{type(result).__name__}"],
                    )
                continue
            decisions.update(result)
            decided = set(result)
            for candidate, _, _ in batch:
                if candidate.id not in decided:
                    decisions[candidate.id] = VisualDecision(
                        candidate_id=candidate.id,
                        verdict="uncertain",
                        confidence=Decimal("0"),
                        reasons=["vision_model_omitted_candidate"],
                    )
        for candidate_id, decision in list(decisions.items()):
            if candidate_id in bindings:
                decision = decision.model_copy(update={'evidence_binding':bindings[candidate_id]})
                decisions[candidate_id] = decision
            good, inliers, ratio = geometry.get(
                candidate_id, (0, 0, Decimal("0"))
            )
            strong_template = good >= 20 and inliers >= 15 and ratio >= Decimal("0.25")
            if not strong_template:
                continue
            reasons = list(
                dict.fromkeys(
                    [
                        *decision.reasons,
                        (
                            "geometry_same_template:"
                            f"matches={good},inliers={inliers},ratio={ratio:.3f}"
                        ),
                    ]
                )
            )
            # Geometry must never overturn a semantic ``different`` or
            # ``uncertain`` verdict.  It is retained for audit/debugging only.
            decisions[candidate_id] = decision.model_copy(update={"reasons": reasons})
        return decisions

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
        schema = json.dumps(VisualComparisonResult.model_json_schema(), ensure_ascii=False)
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
                    "大星形、细颗粒、方格、碎冰、波纹是不同纹理，不能因相同照片或限编就填same。"
                    "黑白/负片印刷与彩色照片不同；正常彩虹反光并不改变印刷照片颜色。"
                    "看不清任一对象的纹理就填unknown。两项均same才允许verdict=same。"
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
                        "text": f"候选编号 {label}：{candidate.title}",
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
        parsed = VisualComparisonResult.model_validate_json(result)
        return {
            labels[decision.candidate_id].id: decision.model_copy(
                update={
                    "candidate_id": labels[decision.candidate_id].id,
                    "confidence": min(decision.confidence, Decimal("0.79")),
                }
            )
            if low_resolution_target
            else decision.model_copy(
                update={"candidate_id": labels[decision.candidate_id].id}
            )
            for raw_decision in parsed.decisions
            for decision in [enforce_visual_observations(raw_decision)]
            if decision.candidate_id in labels
        }
