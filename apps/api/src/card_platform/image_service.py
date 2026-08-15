import base64
import json
import re
from decimal import Decimal
import time
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import aiofiles
import httpx
from fastapi import UploadFile
from openai import AsyncOpenAI
from psycopg import AsyncConnection

from card_platform.config import Settings
from card_platform.card_search_service import CardSearchService
from card_platform.card_orientation import (
    OrientationDecision,
    rotation_from_upright_index,
    rotate_image_content,
)
from card_platform.image_rectification import rectify_card_image
from card_platform import local_ocr
from card_platform.repository import NotFoundError
from card_platform.schemas import (
    CardRecognitionResult,
    ImageUploadResult,
    SheetCardRecognitionResult,
)
from card_platform.vision_routing import TieredVisionRouter, finalize_recognition_status

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def apply_local_ocr_anchors(
    result: CardRecognitionResult, anchors: dict, latency_ms: int
) -> CardRecognitionResult:
    if not anchors:
        return result
    updates = {
        field: anchors[field]
        for field in (
            "season_start", "season_end", "card_number", "serial_number",
            "serial_total", "is_numbered", "is_rookie", "is_autograph",
            "is_memorabilia", "memorabilia_usage",
        )
        if field in anchors
    }
    evidence = {**result.field_evidence}
    evidence_map = {
        "season_start": "season_evidence", "season_end": "season_evidence",
        "card_number": "card_number_evidence", "serial_number": "serial_evidence",
        "serial_total": "serial_evidence", "is_rookie": "rookie_evidence",
        "is_autograph": "autograph_evidence", "is_memorabilia": "material_evidence",
        "memorabilia_usage": "material_evidence",
    }
    for field in updates:
        source = anchors.get(evidence_map.get(field, ""))
        if source:
            evidence[field] = [*evidence.get(field, []), f"Apple Vision OCR：{source}"]
    trace = [{
        "tier": "local_ocr", "model": "apple_vision", "reason": "free_local_anchor_pass",
        "status": "succeeded", "latency_ms": latency_ms, "estimated_cost_cny": "0",
        "anchored_fields": sorted(updates),
    }, *result.recognition_route]
    return result.model_copy(update={**updates, "field_evidence": evidence,
                                     "recognition_route": trace})


def has_valid_signature(content: bytes, content_type: str) -> bool:
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return False


def detect_image_content_type(content: bytes) -> str | None:
    """Detect only the image formats accepted by the platform."""
    for content_type in ALLOWED_IMAGE_TYPES:
        if has_valid_signature(content, content_type):
            return content_type
    return None


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
            """
            SELECT id, grade_company FROM collection_item
            WHERE id = %s AND owner_id = %s AND deleted_at IS NULL
            """,
            (item_id, owner_id),
        )
        item_row = await item.fetchone()
        if item_row is None:
            raise NotFoundError("collection item not found")
        content = await read_validated_image(upload, self.max_bytes)
        return await self._save_content(
            connection,
            item_id,
            side,
            content,
            upload.content_type or "",
            standardize_ratio=not bool(item_row.get("grade_company")),
        )

    async def save_remote_item_image(
        self,
        connection: AsyncConnection,
        item_id: UUID,
        owner_id: UUID,
        side: str,
        image_url: str,
    ) -> ImageUploadResult:
        if side not in {"front", "back"}:
            raise InvalidImageError("side must be front or back")
        item = await connection.execute(
            """
            SELECT id, grade_company FROM collection_item
            WHERE id = %s AND owner_id = %s AND deleted_at IS NULL
            """,
            (item_id, owner_id),
        )
        item_row = await item.fetchone()
        if item_row is None:
            raise NotFoundError("collection item not found")
        parsed = httpx.URL(image_url)
        if parsed.scheme != "https" or parsed.host != "images.cardhobby.com":
            raise InvalidImageError("reference image source is not allowed")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(image_url)
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise InvalidImageError("reference image download failed") from error
        content = response.content
        if not content or len(content) > self.max_bytes:
            raise InvalidImageError("reference image is empty or exceeds the size limit")
        actual_content_type = detect_image_content_type(content)
        if actual_content_type is None:
            raise InvalidImageError("reference image content is invalid")
        content_type = actual_content_type
        return await self._save_content(
            connection,
            item_id,
            side,
            content,
            content_type,
            standardize_ratio=not bool(item_row.get("grade_company")),
        )

    async def _save_content(
        self,
        connection: AsyncConnection,
        item_id: UUID,
        side: str,
        content: bytes,
        content_type: str,
        *,
        standardize_ratio: bool = True,
    ) -> ImageUploadResult:
        rectified = rectify_card_image(
            content,
            content_type,
            standardize_ratio=standardize_ratio,
        )
        content = rectified.content
        content_type = rectified.content_type
        suffix = ALLOWED_IMAGE_TYPES[content_type]
        filename = f"{item_id}-{side}-{uuid4().hex}{suffix}"
        destination = self.upload_dir / filename
        async with aiofiles.open(destination, "wb") as handle:
            await handle.write(content)
        column = "image_front_url" if side == "front" else "image_back_url"
        image_url = f"/uploads/{filename}"
        updated = await connection.execute(
            f"""
            UPDATE collection_item SET {column} = %s, updated_at = now()
            WHERE id = %s AND deleted_at IS NULL
            RETURNING id
            """,
            (image_url, item_id),
        )
        if await updated.fetchone() is None:
            destination.unlink(missing_ok=True)
            raise NotFoundError("collection item not found")
        return ImageUploadResult(item_id=item_id, side=side, image_url=image_url)

    async def rotate_item_image(
        self,
        connection: AsyncConnection,
        item_id: UUID,
        owner_id: UUID,
        side: str,
        degrees_clockwise: int,
    ) -> ImageUploadResult:
        if side not in {"front", "back"}:
            raise InvalidImageError("side must be front or back")
        if degrees_clockwise not in {90, 180, 270}:
            raise InvalidImageError("degrees must be 90, 180, or 270")
        column = "image_front_url" if side == "front" else "image_back_url"
        selected = await connection.execute(
            f"""
            SELECT {column} AS image_url FROM collection_item
            WHERE id = %s AND owner_id = %s AND deleted_at IS NULL
            """,
            (item_id, owner_id),
        )
        row = await selected.fetchone()
        if row is None:
            raise NotFoundError("collection item not found")
        old_url = row.get("image_url")
        old_path = self._local_item_image_path(item_id, side, old_url)
        if old_path is None or not old_path.is_file():
            raise InvalidImageError("card image is not stored locally")
        content = rotate_image_content(old_path.read_bytes(), degrees_clockwise)
        filename = f"{item_id}-{side}-{uuid4().hex}.jpg"
        destination = self.upload_dir / filename
        async with aiofiles.open(destination, "wb") as handle:
            await handle.write(content)
        image_url = f"/uploads/{filename}"
        updated = await connection.execute(
            f"""
            UPDATE collection_item SET {column} = %s, updated_at = now()
            WHERE id = %s AND owner_id = %s AND deleted_at IS NULL
            RETURNING id
            """,
            (image_url, item_id, owner_id),
        )
        if await updated.fetchone() is None:
            destination.unlink(missing_ok=True)
            raise NotFoundError("collection item not found")
        await connection.execute(
            """
            INSERT INTO card_image_orientation_correction (
                collection_item_id, owner_id, side, rotation_degrees
            ) VALUES (%s, %s, %s, %s)
            """,
            (item_id, owner_id, side, degrees_clockwise),
        )
        old_path.unlink(missing_ok=True)
        return ImageUploadResult(item_id=item_id, side=side, image_url=image_url)

    def _local_item_image_path(
        self, item_id: UUID, side: str, image_url: str | None
    ) -> Path | None:
        if not image_url or not image_url.startswith("/uploads/"):
            return None
        filename = image_url.removeprefix("/uploads/")
        pattern = re.compile(
            rf"^{re.escape(str(item_id))}-{side}-[0-9a-f]{{32}}\.(?:jpg|png|webp)$"
        )
        if "/" in filename or not pattern.fullmatch(filename):
            return None
        candidate = (self.upload_dir / filename).resolve()
        return candidate if candidate.parent == self.upload_dir else None

    def delete_item_images(self, item_id: UUID, image_urls: tuple[str | None, str | None]) -> None:
        pattern = re.compile(
            rf"^{re.escape(str(item_id))}-(?:front|back)-[0-9a-f]{{32}}"
            r"\.(?:jpg|png|webp)$"
        )
        for image_url in image_urls:
            if not image_url or not image_url.startswith("/uploads/"):
                continue
            filename = image_url.removeprefix("/uploads/")
            if "/" in filename or not pattern.fullmatch(filename):
                continue
            candidate = (self.upload_dir / filename).resolve()
            if candidate.parent != self.upload_dir:
                continue
            candidate.unlink(missing_ok=True)


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

    async def detect_orientation(
        self, content: bytes, content_type: str
    ) -> OrientationDecision:
        rotations = [rotate_image_content(content, angle) for angle in (0, 90, 180, 270)]
        user_content: list[dict] = [
            {
                "type": "text",
                "text": (
                    "下面四张依次是同一原图顺时针旋转 0、90、180、270 度的版本。"
                    "选择真正正立的一张，返回 upright_image_index（1 到 4）和 confidence。"
                ),
            }
        ]
        for image in rotations:
            encoded = base64.b64encode(image).decode("ascii")
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                }
            )
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "比较同一张球星卡的四个像素旋转版本，选择文字、球员和版式真正正立的一张。"
                        "只输出 JSON：upright_image_index 必须为 1、2、3、4 之一；"
                        "confidence 为 0 到 1。不要按图片宽高猜测，必须依据文字方向、人物姿态、"
                        "品牌标志和卡面布局综合判断。"
                    ),
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=200,
            extra_body={"enable_thinking": False},
        )
        raw = response.choices[0].message.content
        if not raw:
            raise RuntimeError("vision orientation response was empty")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if match is None:
                raise RuntimeError("vision orientation response was not valid JSON")
            payload = json.loads(match.group(0))
        rotation = rotation_from_upright_index(payload.get("upright_image_index"))
        confidence = max(0.0, min(1.0, float(payload.get("confidence", 0))))
        return OrientationDecision(rotation, confidence, "vision_model")

    async def recognize_images(
        self,
        images: list[tuple[bytes, str]],
        prompt: str,
    ) -> CardRecognitionResult:
        result, _ = await self.recognize_images_with_usage(images, prompt)
        return result

    async def recognize_images_with_usage(
        self,
        images: list[tuple[bytes, str]],
        prompt: str,
        prior_result: str | None = None,
    ) -> tuple[CardRecognitionResult, dict[str, int | None]]:
        user_content: list[dict] = [{"type": "text", "text": prompt}]
        if prior_result:
            user_content.append({
                "type": "text",
                "text": (
                    "低成本模型的初步结果如下。请独立复核图片，重点纠正缺失、冲突、"
                    "卡号与限编混淆及泛化平行名；不要无依据照抄：" + prior_result
                ),
            })
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
                        "置信度。image_role 只能是 front、back、ad、unknown；多图联合"
                        "识别时填 unknown。只输出一个精简 JSON 对象，允许的键只有："
                        "image_role、player_name、"
                        "season_start、season_end、copyright_year、brand_name、"
                        "product_name、insert_name、"
                        "parallel_name、card_number、serial_number、serial_total、is_rookie、"
                        "is_numbered、grade_company、grade_value、rookie_evidence、"
                        "is_autograph、autograph_type、has_inscription、inscription_text、"
                        "is_memorabilia、memorabilia_types、"
                        "memorabilia_usage、memorabilia_player、is_patch、confidence、"
                        "caveats、field_evidence。"
                        "无法从图片确认的布尔字段必须为 null，确认后才填 true/false；"
                        "confidence 为 0 到 1；caveats 为字符串"
                        "数组；field_evidence 为字段名到可见依据字符串数组的映射。不得输出"
                        "候选列表、搜索词、参考图或额外解释。"
                        "字段分类必须严格：brand_name 是厂商（如 Panini、Topps）；"
                        "product_name 是产品线（如 Select、Donruss、National Treasures）；"
                        "insert_name 是产品内特卡/子集（如 Lodestar、Night Moves）；"
                        "parallel_name 是颜色或折射版本（如 Red、Gold、X-Fractor）。"
                        "Mosaic 基础卡背面的 PRIZM 只表示折射版本，不是 insert；除非卡面或"
                        "卡背另有明确子集名称，否则 insert_name=null。Mosaic 限编卡应结合"
                        "正面颜色、x/y 限编和 checklist 识别具体 parallel（例如 /88 的"
                        "Fusion Red and Yellow），不能把 insert_name 和 parallel_name 都写成"
                        "泛化的 Prizm。"
                        "Prizm、Select、Obsidian、National Treasures、Immaculate、"
                        "Flawless、Donruss、Optic、Mosaic、Noir、Recon、Threads、"
                        "Hoops 均是 Panini"
                        "产品线，不是厂商；Chrome、Finest、Inception 是 Topps 产品线。"
                        "NBA Hoops 的 SLAM 特卡会故意模仿 SLAM 杂志封面，必须结合"
                        "卡背 Panini、HOOPS、赛季和 No. 卡号识别，不能当成真实杂志。"
                        "Threads 的球衣形状 Team Threads 模切不等于物料卡；只有真实"
                        "物料切片或卡背 enclosed material 声明才算 memorabilia。"
                        "copyright_year 只读取卡背版权行中的 © 年份。赛季必须来自产品标题、"
                        "评级标签或 checklist；球员统计表、履历、选秀年份、DRAFTED 字样"
                        "绝不能作为 season_start/season_end。"
                        "RC 只能体现在 is_rookie，不能写入 insert_name 或 parallel_name；"
                        "rookie_evidence 是字符串数组，记录可见的 RC 标志、评级标签或"
                        "可靠背面文字；没有可靠依据时返回空数组。"
                        "03/10、1/1 等是限编，不是卡号或平行名称。看到 x/y 时"
                        "is_numbered=true、serial_number=x、serial_total=y；明确无编号时"
                        "is_numbered=false，不能确认时为 null。Panini One and One 是"
                        "产品名称，单独出现的英文 One and One 绝不是 1/1 限编。"
                        "is_autograph 表示是否亲笔签名；autograph_type 只能是 on_card、"
                        "sticker、cut、unknown、none。必须区分卡签、贴签和印刷签名；"
                        "没有亲笔签名时填 false/none。"
                        "has_inscription 表示签名旁是否另有球员亲笔铭文；"
                        "inscription_text 必须逐字读取图片中实际可见的铭文，绝不能复用"
                        "提示词、历史请求或其他卡片的文字。"
                        "球员签名本身不是铭文；无法辨认内容时 has_inscription=true、"
                        "inscription_text=null，并记录图片证据。"
                        "is_memorabilia 表示是否嵌有球衣、patch、球、鞋、地板等物料；"
                        "memorabilia_types 是标准英文类型数组；is_patch 仅表示 patch。"
                        "memorabilia_usage 只能是 game_used、player_worn、event_worn、"
                        "not_from_specific_game_or_event、manufactured、unknown、none；"
                        "必须依据卡背声明，不能因正面有物料窗就猜亲穿或比赛使用。"
                        "memorabilia_player 填卡背明确声明的物料所属球员，否则为 null。"
                        "若卡片装在评级壳中，grade_company 读取 PSA/BGS/CGC/SGC 等公司，"
                        "grade_value 读取标签总分；裸卡两个字段必须为 null。"
                    ),
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=1200,
            extra_body={"enable_thinking": False},
        )
        result = response.choices[0].message.content
        if not result:
            raise RuntimeError("vision response did not contain structured card data")
        parsed = CardRecognitionResult.model_validate_json(result)
        usage = response.usage
        return parsed, {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
        }

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
            extra_body={"enable_thinking": False},
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
        self.tiered_providers = None
        if provider is None and settings.vision_api_key:
            self.tiered_providers = {
                "flash": self.provider,
                "plus": QwenVisionProvider(
                    settings.vision_api_key, settings.vision_base_url,
                    settings.vision_plus_model, settings.vision_timeout_seconds,
                ),
                "max": QwenVisionProvider(
                    settings.vision_api_key, settings.vision_base_url,
                    settings.vision_max_model, settings.vision_timeout_seconds,
                ) if settings.vision_max_enabled else None,
            }
        self.plus_threshold = settings.vision_plus_threshold
        self.max_threshold = settings.vision_max_threshold

    async def recognize(self, upload: UploadFile) -> CardRecognitionResult:
        if self.provider is None:
            raise RuntimeError(
                "VISION_API_KEY is required for Qwen3-VL-Flash card image recognition"
            )
        content = await read_validated_image(upload, self.max_bytes)
        visual = await self.provider.recognize(content, upload.content_type or "image/jpeg")
        return finalize_recognition_status(await self.search.enrich(visual))

    async def recognize_pair(
        self, front: UploadFile, back: UploadFile
    ) -> CardRecognitionResult:
        if self.provider is None:
            raise RuntimeError(
                "VISION_API_KEY is required for Qwen3-VL-Flash card image recognition"
            )
        front_content = await read_validated_image(front, self.max_bytes)
        back_content = await read_validated_image(back, self.max_bytes)
        return await self.recognize_pair_contents(
            front_content,
            front.content_type or "image/jpeg",
            back_content,
            back.content_type or "image/jpeg",
        )

    async def recognize_pair_contents(
        self,
        front_content: bytes,
        front_content_type: str,
        back_content: bytes,
        back_content_type: str,
    ) -> CardRecognitionResult:
        if self.provider is None:
            raise RuntimeError(
                "VISION_API_KEY is required for Qwen3-VL-Flash card image recognition"
            )
        if not hasattr(self.provider, "recognize_images"):
            raise RuntimeError("configured vision provider does not support paired recognition")
        images = [
            (front_content, front_content_type),
            (back_content, back_content_type),
        ]
        ocr_started = time.monotonic()
        ocr_lines = await local_ocr.recognize_images(images)
        ocr_latency_ms = round((time.monotonic() - ocr_started) * 1000)
        ocr_anchors = local_ocr.extract_anchors(ocr_lines)
        prompt = (
                "这两张图片依次是同一张篮球球星卡的正面和背面。联合识别，优先从"
                "背面读取年份、卡号和版权信息，从正面判断球员、卡种与视觉属性；"
                "冲突字段写入 caveats，不要把两张图当成两张卡。"
        )
        if ocr_lines:
            prompt += (
                "\n以下是本机 Apple Vision OCR 原文和经过格式校验的锚点。OCR 可能有误，"
                "但卡号与 x/y 限编不得混淆；如图片明确冲突必须写入 caveats："
                + json.dumps({"ocr_lines": ocr_lines, "validated_anchors": ocr_anchors},
                             ensure_ascii=False)
            )
        if self.tiered_providers:
            def routed_call(provider):
                if provider is None:
                    return None
                return lambda prior: provider.recognize_images_with_usage(
                    images, prompt, prior_result=prior
                )

            router = TieredVisionRouter(
                routed_call(self.tiered_providers["flash"]),
                routed_call(self.tiered_providers["plus"]),
                routed_call(self.tiered_providers["max"]),
                plus_threshold=Decimal(str(self.plus_threshold)),
                max_threshold=Decimal(str(self.max_threshold)),
            )
            visual = await router.recognize()
        else:
            visual = await self.provider.recognize_images(images, prompt)
        visual = apply_local_ocr_anchors(visual, ocr_anchors, ocr_latency_ms)
        return finalize_recognition_status(await self.search.enrich(visual))

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
