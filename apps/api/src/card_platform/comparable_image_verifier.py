import base64
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import httpx
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
    MAX_IMAGE_BYTES = 8 * 1024 * 1024

    def __init__(self, settings: Settings) -> None:
        self.upload_dir = Path(settings.upload_dir).resolve()
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
        path = (self.upload_dir / filename).resolve()
        return path if path.parent == self.upload_dir and path.is_file() else None

    @staticmethod
    def _data_url(content: bytes, content_type: str) -> str:
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    async def verify(
        self,
        target_image_url: str | None,
        candidates: list[VisualCandidate],
    ) -> dict[str, VisualDecision]:
        target_path = self._target_path(target_image_url)
        if self.client is None or target_path is None or not candidates:
            return {}
        target = target_path.read_bytes()
        if len(target) > self.MAX_IMAGE_BYTES:
            return {}
        target_type = "image/png" if target_path.suffix.casefold() == ".png" else "image/jpeg"
        downloaded: list[tuple[VisualCandidate, bytes, str]] = []
        headers = {"User-Agent": "Mozilla/5.0 CardPlatform/1.0"}
        async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as client:
            for candidate in candidates[: self.MAX_CANDIDATES]:
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
        if not downloaded:
            return {}
        schema = json.dumps(VisualComparisonResult.model_json_schema(), ensure_ascii=False)
        content: list[dict] = [
            {
                "type": "text",
                "text": (
                    "第一张图片是目标卡。后续图片按候选编号排列。逐张判断候选是否与目标"
                    "属于同一球员、同一产品、同一正式卡型/insert、同一平行层级和同一"
                    "限编总数。流水号不同允许；图片裁切、角度、亮度不同允许。重点比较卡面"
                    "模板、物料窗数量与形状、签字区、Logo、边框和文字位置。明显为大窗与"
                    "三窗、不同 insert、不同平行或不同限编时 verdict=different。仅因图片"
                    "模糊无法判断时 verdict=uncertain，不能猜 same。"
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": self._data_url(target, target_type)},
            },
        ]
        for candidate, image, content_type in downloaded:
            content.extend(
                [
                    {
                        "type": "text",
                        "text": f"候选 {candidate.id}：{candidate.title}",
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
        )
        result = response.choices[0].message.content
        if not result:
            return {}
        parsed = VisualComparisonResult.model_validate_json(result)
        allowed = {candidate.id for candidate, _, _ in downloaded}
        return {
            decision.candidate_id: decision
            for decision in parsed.decisions
            if decision.candidate_id in allowed
        }
