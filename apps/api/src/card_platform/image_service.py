import base64
import json
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import aiofiles
from fastapi import UploadFile
from openai import AsyncOpenAI
from psycopg import AsyncConnection

from card_platform.config import Settings
from card_platform.card_search_service import CardSearchService
from card_platform.repository import NotFoundError
from card_platform.schemas import (
    CardRecognitionResult,
    ImageUploadResult,
    SheetCardRecognitionResult,
)

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def has_valid_signature(content: bytes, content_type: str) -> bool:
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return False


class InvalidImageError(ValueError):
    pass


async def read_validated_image(upload: UploadFile, max_bytes: int) -> bytes:
    if upload.content_type not in ALLOWED_IMAGE_TYPES:
        raise InvalidImageError("image must be JPEG, PNG, or WebP")
    content = await upload.read(max_bytes + 1)
    if not content:
        raise InvalidImageError("image is empty")
    if len(content) > max_bytes:
        raise InvalidImageError(f"image exceeds {max_bytes} bytes")
    if not has_valid_signature(content, upload.content_type or ""):
        raise InvalidImageError("image content does not match its declared type")
    return content


class ImageStorageService:
    def __init__(self, settings: Settings) -> None:
        self.upload_dir = Path(settings.upload_dir).resolve()
        self.max_bytes = settings.max_image_bytes
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    async def save_item_image(
        self,
        connection: AsyncConnection,
        item_id: UUID,
        owner_id: UUID,
        side: str,
        upload: UploadFile,
    ) -> ImageUploadResult:
        if side not in {"front", "back"}:
            raise InvalidImageError("side must be front or back")
        item = await connection.execute(
            "SELECT id FROM collection_item WHERE id = %s AND owner_id = %s",
            (item_id, owner_id),
        )
        if await item.fetchone() is None:
            raise NotFoundError("collection item not found")
        content = await read_validated_image(upload, self.max_bytes)
        suffix = ALLOWED_IMAGE_TYPES[upload.content_type or ""]
        filename = f"{item_id}-{side}-{uuid4().hex}{suffix}"
        destination = self.upload_dir / filename
        async with aiofiles.open(destination, "wb") as handle:
            await handle.write(content)
        column = "image_front_url" if side == "front" else "image_back_url"
        image_url = f"/uploads/{filename}"
        await connection.execute(
            f"UPDATE collection_item SET {column} = %s, updated_at = now() WHERE id = %s",
            (image_url, item_id),
        )
        return ImageUploadResult(item_id=item_id, side=side, image_url=image_url)


class VisionProvider(Protocol):
    async def recognize(self, content: bytes, content_type: str) -> CardRecognitionResult: ...


class QwenVisionProvider:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60) -> None:
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=1,
        )
        self.model = model

    async def recognize(self, content: bytes, content_type: str) -> CardRecognitionResult:
        return await self.recognize_images(
            [(content, content_type)],
            "请识别这张篮球球星卡。",
        )

    async def recognize_images(
        self,
        images: list[tuple[bytes, str]],
        prompt: str,
    ) -> CardRecognitionResult:
        schema = json.dumps(CardRecognitionResult.model_json_schema(), ensure_ascii=False)
        user_content: list[dict] = [{"type": "text", "text": prompt}]
        for content, content_type in images:
            encoded = base64.b64encode(content).decode("ascii")
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{content_type};base64,{encoded}"},
                }
            )
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "识别篮球球星卡的可见信息。只填写能从卡面可靠判断的字段；"
                        "无法确认时返回 null 并在 caveats 说明。不要根据球员生涯年份猜测"
                        "卡片年份，不要把印刷签名误判为亲笔签名。confidence 是整体识别"
                        f"置信度。只输出符合以下 JSON Schema 的 JSON：{schema}"
                    ),
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            response_format={"type": "json_object"},
        )
        result = response.choices[0].message.content
        if not result:
            raise RuntimeError("vision response did not contain structured card data")
        return CardRecognitionResult.model_validate_json(result)

    async def recognize_sheet(
        self, content: bytes, content_type: str
    ) -> SheetCardRecognitionResult:
        schema = json.dumps(SheetCardRecognitionResult.model_json_schema(), ensure_ascii=False)
        encoded = base64.b64encode(content).decode("ascii")
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你在解析一张可能同时包含多张篮球球星卡、订单行或商品列表的图片。"
                        "先按视觉分区和订单/商品边界确定独立卡牌数量，再为每张卡分别提取卡片"
                        "身份和对应的采购信息，严格保持从上到下、从左到右的顺序。价格必须绑定"
                        "到同一商品行；purchase_price 只填商品价，shipping_price 单独填写。"
                        "card_image_bbox 必须框住该条目中卡片实物图片本身，不要包含标题、价格、"
                        "订单信息或页面背景；坐标按整张图片宽高归一化到 0 到 1000，格式为"
                        "[left, top, right, bottom]。如果没有可见卡片实物图则返回 null。"
                        "同时完整 OCR 同一商品行中与卡片有关的全部标题和描述：listing_title"
                        "填写主标题，visible_description 填写其余描述原文。卡面无法确认的年份、"
                        "球员、品牌、产品线、卡种、平行版本、卡号、限编、RC、签字或 Patch，可用"
                        "这些文字补全 result，并在 text_derived_fields 中以字段名记录对应的原文"
                        "证据。文字与卡面冲突时以卡面为准，并把冲突写入 caveats；不要把卖家宣传"
                        "词直接当作事实，也不要把商品编号或订单号当成卡号。"
                        "中文译名要尽量还原为英文球员名。无法可靠确认的字段返回 null，不能把"
                        "卖家名、订单号或商品编号当作卡号。detected_count 必须等于 cards 数量。"
                        f"只输出符合以下 JSON Schema 的 JSON：{schema}"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "识别图片中的全部球星卡并逐条返回。"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{content_type};base64,{encoded}"},
                        },
                    ],
                },
            ],
            response_format={"type": "json_object"},
        )
        result = response.choices[0].message.content
        if not result:
            raise RuntimeError("vision response did not contain structured multi-card data")
        return SheetCardRecognitionResult.model_validate_json(result)


class CardVisionService:
    def __init__(self, settings: Settings, provider: VisionProvider | None = None) -> None:
        self.max_bytes = settings.max_image_bytes
        self.search = CardSearchService(settings)
        self.provider = provider or (
            QwenVisionProvider(
                settings.vision_api_key,
                settings.vision_base_url,
                settings.vision_model,
                settings.vision_timeout_seconds,
            )
            if settings.vision_api_key
            else None
        )

    async def recognize(self, upload: UploadFile) -> CardRecognitionResult:
        if self.provider is None:
            raise RuntimeError(
                "VISION_API_KEY is required for Qwen3-VL-Flash card image recognition"
            )
        content = await read_validated_image(upload, self.max_bytes)
        visual = await self.provider.recognize(content, upload.content_type or "image/jpeg")
        return await self.search.enrich(visual)

    async def recognize_pair(
        self, front: UploadFile, back: UploadFile
    ) -> CardRecognitionResult:
        if self.provider is None:
            raise RuntimeError(
                "VISION_API_KEY is required for Qwen3-VL-Flash card image recognition"
            )
        front_content = await read_validated_image(front, self.max_bytes)
        back_content = await read_validated_image(back, self.max_bytes)
        if not hasattr(self.provider, "recognize_images"):
            raise RuntimeError("configured vision provider does not support paired recognition")
        visual = await self.provider.recognize_images(
            [
                (front_content, front.content_type or "image/jpeg"),
                (back_content, back.content_type or "image/jpeg"),
            ],
            (
                "这两张图片依次是同一张篮球球星卡的正面和背面。联合识别，优先从"
                "背面读取年份、卡号和版权信息，从正面判断球员、卡种与视觉属性；"
                "冲突字段写入 caveats，不要把两张图当成两张卡。"
            ),
        )
        return await self.search.enrich(visual)

    async def recognize_sheet(self, upload: UploadFile) -> SheetCardRecognitionResult:
        if self.provider is None:
            raise RuntimeError(
                "VISION_API_KEY is required for Qwen3-VL-Flash card image recognition"
            )
        content = await read_validated_image(upload, self.max_bytes)
        if not hasattr(self.provider, "recognize_sheet"):
            raise RuntimeError("configured vision provider does not support multi-card recognition")
        sheet = await self.provider.recognize_sheet(
            content, upload.content_type or "image/jpeg"
        )
        for card in sheet.cards:
            context_parts = [card.listing_title, card.visible_description]
            context = "\n".join(part for part in context_parts if part)
            if card.text_derived_fields:
                card.result = card.result.model_copy(
                    update={
                        "field_evidence": {
                            **card.result.field_evidence,
                            **{
                                field: [f"截图文字：{evidence}"]
                                for field, evidence in card.text_derived_fields.items()
                            },
                        }
                    }
                )
            card.result = await self.search.enrich(card.result, context=context or None)
        return sheet
