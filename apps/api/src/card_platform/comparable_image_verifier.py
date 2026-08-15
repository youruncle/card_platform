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


class VisualComparisonResult(BaseModel):
    decisions: list[VisualDecision] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class VisualCandidate:
    id: str
    title: str
    image_url: str


class ComparableImageVerifier:
    # Vision models become materially less reliable when asked to compare one
    # reference against a large contact sheet. Keep the batch small; explicit
    # text matches/conflicts are handled independently by the classifier.
    MAX_CANDIDATES = 3
    MAX_TOTAL_CANDIDATES = 12
    MAX_IMAGE_BYTES = 8 * 1024 * 1024
    MIN_TARGET_EDGE = 300

    def __init__(self, settings: Settings) -> None:
        configured = Path(settings.upload_dir)
        roots = [configured]
        if not configured.is_absolute():
            # The API is started both from `apps/api` and from the repository root.
            # Uploaded image URLs must resolve identically in both cases.
            roots.append(Path("apps/api") / configured)
        self.upload_dirs = tuple(dict.fromkeys(root.resolve() for root in roots))
        self.model = settings.vision_model
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
        downloaded: list[tuple[VisualCandidate, bytes, str]] = []
        geometry: dict[str, tuple[int, int, Decimal]] = {}
        requested = candidates[: self.MAX_TOTAL_CANDIDATES]
        headers = {"User-Agent": "Mozilla/5.0 CardPlatform/1.0"}
        async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as client:
            for candidate in requested:
                parsed = urlparse(candidate.image_url)
                if parsed.scheme != "https" or parsed.hostname != "images.cardhobby.com":
                    continue
                try:
                    response = await client.get(candidate.image_url)
                    response.raise_for_status()
                except httpx.HTTPError:
                    continue
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if (
                    content_type not in {"image/jpeg", "image/png", "image/webp"}
                    or len(response.content) > self.MAX_IMAGE_BYTES
                ):
                    continue
                downloaded.append((candidate, response.content, content_type))
                geometry[candidate.id] = await asyncio.to_thread(
                    self._geometric_template_score, target_image, response.content
                )
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
        model_candidates: list[tuple[VisualCandidate, bytes, str]] = []
        for candidate, image, content_type in downloaded:
            good, inliers, ratio = geometry[candidate.id]
            strong_template = good >= 20 and inliers >= 15 and ratio >= Decimal("0.25")
            if strong_template and not requires_parallel_match:
                decisions[candidate.id] = VisualDecision(
                    candidate_id=candidate.id,
                    verdict="compatible",
                    confidence=Decimal("0.95"),
                    reasons=[
                        "local_geometry_verified_same_printed_template",
                        f"geometry_same_template:matches={good},inliers={inliers},ratio={ratio:.3f}",
                    ],
                )
            else:
                model_candidates.append((candidate, image, content_type))
        batches = [
            model_candidates[index : index + self.MAX_CANDIDATES]
            for index in range(0, len(model_candidates), self.MAX_CANDIDATES)
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
            good, inliers, ratio = geometry.get(
                candidate_id, (0, 0, Decimal("0"))
            )
            strong_template = good >= 20 and inliers >= 15 and ratio >= Decimal("0.25")
            if not strong_template:
                continue
            reasons = [
                *decision.reasons,
                f"geometry_same_template:matches={good},inliers={inliers},ratio={ratio:.3f}",
            ]
            if decision.verdict in {"different", "uncertain"}:
                # Geometry can prove the same printed template despite foil glare,
                # but not the exact parallel coating. The identity classifier keeps
                # this T0 only when no explicit parallel distinction is required.
                decisions[candidate_id] = decision.model_copy(
                    update={
                        "verdict": "compatible",
                        "confidence": max(decision.confidence, Decimal("0.90")),
                        "reasons": reasons,
                    }
                )
            else:
                decisions[candidate_id] = decision.model_copy(
                    update={"reasons": reasons}
                )
        return decisions

    async def _compare_batch(
        self,
        target: bytes,
        target_type: str,
        downloaded: list[tuple[VisualCandidate, bytes, str]],
        *,
        low_resolution_target: bool,
    ) -> dict[str, VisualDecision]:
        schema = json.dumps(VisualComparisonResult.model_json_schema(), ensure_ascii=False)
        content: list[dict] = [
            {
                "type": "text",
                "text": (
                    "第一张图片是目标卡。后续图片按候选编号排列。逐张判断候选是否与目标"
                    "属于同一球员、同一产品、同一正式卡型/insert、同一平行层级和同一"
                    "限编总数。先分别找出卡片四角，按卡片自身宽高把关键点坐标归一化，"
                    "不要用整张照片的坐标。必须逐项比较：球衣号码中心点的横纵坐标、号码"
                    "整体倾斜角度、号码是否发生透视拉伸、人物头肩和双手姿态、人物在卡面"
                    "左右位置、Logo、卡名和奖项文字位置。尤其不能把同系列中号码位置或"
                    "人物姿态不同的两张卡判为同卡。请在 reasons 中明确写出观察到的号码"
                    "位置（例如中间偏右/左下）、倾斜方向和姿态差异。流水号不同允许；"
                    "图片裁切、拍摄角度、亮度不同允许。重点比较卡面模板、物料窗数量与"
                    "形状、签字区、Logo、边框和文字位置。明显为大窗与"
                    "三窗、不同 insert、不同平行或不同限编时 verdict=different。仅因图片"
                    "模糊无法判断时 verdict=uncertain，不能猜 same。"
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
                "image_url": {"url": self._data_url(target, target_type)},
            },
        ]
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
                        "image_url": {"url": self._data_url(image, content_type)},
                    },
                ]
            )
        response = await self.client.chat.completions.create(
            model=self.model,
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
            for decision in parsed.decisions
            if decision.candidate_id in labels
        }
