"""Bounded public photo capture and a title-independent physical-card reader."""

import base64
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import tempfile

import httpx
from openai import AsyncOpenAI
from PIL import Image

from card_platform.ingestion.cardhobby_public_item import (
    parse_public_item_html,
    public_gallery_for_primary,
)
from card_platform.model_requests import nonthinking_extra_body
from card_platform.player_prices.image_evidence import (
    make_receipt,
    gallery_primary,
)
from card_platform.public_source_catalog import validated_sale
from card_platform.purchase_cost_images import MAX_BYTES, read_bounded, validate_photo
from card_platform.valuation_trace import traced_completion


class EvidenceBlobs:
    def __init__(self, root):
        self.root = Path(root)
        if self.root.is_symlink():
            raise ValueError("invalid_price_evidence_directory")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def read(self, sha):
        if not isinstance(sha, str) or not re.fullmatch("[a-f0-9]{64}", sha):
            raise ValueError("invalid_price_blob_key")
        path = self.root / (sha + ".blob")
        if path.is_symlink() or not 0 < path.stat().st_size <= MAX_BYTES:
            raise ValueError("invalid_price_blob")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != sha:
            raise ValueError("price_blob_changed")
        return content

    def put(self, content):
        if not 0 < len(content) <= MAX_BYTES:
            raise ValueError("invalid_price_blob_size")
        sha = hashlib.sha256(content).hexdigest()
        path = self.root / (sha + ".blob")
        if path.exists():
            self.read(sha)
            return sha
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as stream:
                temporary = stream.name
                os.chmod(temporary, 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return sha


def image_prompt():
    from card_platform.player_prices.image_observations import FaceFacts

    example = FaceFacts(confidence=0).model_dump(mode="json")
    example["presence"] = {
        name: dict(state="unreadable", detail=None)
        for name in ("autograph", "memorabilia", "patch", "redemption", "serial", "major_damage")
    }
    return (
        "只观察这一张实拍图；没有标题、目标卡款、价格或另一面。输出JSON结构："
        + json.dumps(example, ensure_ascii=False)
        + "。每项必须填写，证据detail至少8字符并写实际位置/可见内容；未知null。confidence是0至1小数。"
        "confidence评估本面实际可见身份与观察的清晰程度，不因本面设计本来没有卡号/发行年或另一面未给"
        "而扣低；这些缺失由相应字段null表示，不能以置信度代替未知字段。"
        "role=front/back/unknown；complete仅指卡片完整可见，card_count实际卡片张数，view_detail写依据。"
        "player_names列卡内全部主体，multi_subject未知null。brand/product独立读取，identity_evidence写原文位置。"
        "presence每项只指本面：present本面存在，absent_in_view本面完整未见，unreadable本面看不清；"
        "不能因另一面未给就把本面清楚未见写unreadable。autograph签字、memorabilia嵌入物料、"
        "patch多色拼接物料、redemption兑换码/兑换卡、serial机器限编烫印，独立观察不自动补其他项。"
        "major_damage仅明显强折痕、折断、撕裂、大面积损坏或缺角；不是保证无暗伤或高分，"
        "普通小白边、日常磨损不算major_damage。卡面完整清晰且未见严重损坏可absent_in_view；"
        "若大片遮挡或反光致无法检查严重损坏才unreadable，不以潜在暗伤为由拒绝可见部位观察。"
        "card_number只抄卡内独立编号并写number_role=collector_number，正面没有就null；"
        "球衣号、统计、贴纸、版权生产码、限编分数不是卡号。serial_stamp只抄唯一机器x/y烫印，"
        "number_role=unique_serial，无印章value=null。release_season只写明确产品发行行或评级标签"
        "赛季原文，并写number_role=release_label；版权/统计/选秀年不是发行标注，不能填入。"
        "surface只描述光学表面，不猜版本名：rainbow=visible/not_visible/unreadable，指彩虹折射，"
        "不是球衣或底图多色；fixed_colors仅固定染色边框的颜色数组，彩虹反光的蓝紫绿等不能写入；"
        "明确没有固定染色为[]，看不清null。pattern=none/cracked_ice/wave/checkerboard/striped/other/unreadable，"
        "指额外固定镭射或平行纹理；原版印刷线框、背景图案、普通金属印刷细点不算额外纹理。"
        "清楚光滑为none，看不清unreadable。背面不套正面表面，detail说位置与实际特征。"
        "finish_markings逐字记录额外版本标签，每条{text,kind,detail}，kind仅实际独立版本标识写variant_mark，"
        "产品Logo写product_logo、版权产品行写copyright，无法确定写unknown，如独立REFRACTOR/PRIZM；"
        "产品Logo、版权产品行不算额外标识。完整看清相关区finish_markings_complete=true；"
        "清楚未见标识填[]，看不清false。grade.status=raw/graded/unknown，company/score/cert_number"
        "仅抄当前图实际评级标签；raw这些字段null，detail说明裸卡或硬壳/标签。"
        "只输出本面观察，不做两面合并，不回答价格。"
    )


class PriceImageReadError(ValueError):
    def __init__(self, code, observations, model_calls):
        super().__init__(code)
        self.observations = observations
        self.model_calls = model_calls
        self.trace_sha256 = None


class PriceImageReader:
    def __init__(self, settings, *, http_client=None):
        if settings.vision_model != "deepseek-flash":
            raise ValueError("price_vision_model_not_supported")
        self.model = settings.vision_model
        self.client = AsyncOpenAI(
            api_key=settings.vision_api_key,
            base_url=settings.vision_base_url,
            timeout=settings.vision_timeout_seconds,
            max_retries=0,
            http_client=http_client,
        )

    async def close(self):
        await self.client.close()

    async def observe(self, images):
        from card_platform.player_prices.image_observations import FaceFacts, compose_faces

        if len(images) != 2:
            raise ValueError("price_pair_images_required")
        for data, mime in images:
            validate_photo(data)
            if mime not in {"image/jpeg", "image/png", "image/webp"}:
                raise ValueError("invalid_price_image_type")
        prompt = image_prompt()
        observations, faces = [], []
        calls = 0
        for index, (data, mime) in enumerate(images):
            calls += 1
            try:
                response = await traced_completion(
                    self.client.chat.completions.create,
                    stage="player_price_face_observation",
                    model=self.model,
                    messages=[
                        dict(
                            role="user",
                            content=[
                                dict(type="text", text=prompt),
                                dict(
                                    type="image_url",
                                    image_url=dict(
                                        url=f"data:{mime};base64," + base64.b64encode(data).decode()
                                    ),
                                ),
                            ],
                        )
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=2500,
                    extra_body=nonthinking_extra_body(self.model),
                )
                raw = response.choices[0].message.content
                usage = response.usage
                observations.append(
                    dict(
                        image_index=index,
                        image_sha256=hashlib.sha256(data).hexdigest(),
                        model=self.model,
                        request_extra=nonthinking_extra_body(self.model),
                        prompt=prompt,
                        raw_response=raw,
                        prompt_tokens=getattr(usage, "prompt_tokens", None),
                        completion_tokens=getattr(usage, "completion_tokens", None),
                    )
                )
                faces.append(FaceFacts.model_validate_json(raw or "").model_dump(mode="json"))
            except Exception as error:
                raise PriceImageReadError(
                    "price_face_observation_failed", observations, calls
                ) from error
        try:
            result = compose_faces(faces)
        except Exception as error:
            raise PriceImageReadError(
                "price_face_composition_failed", observations, calls
            ) from error
        return result, dict(
            model_calls=calls,
            prompt_tokens=sum(o["prompt_tokens"] or 0 for o in observations),
            completion_tokens=sum(o["completion_tokens"] or 0 for o in observations),
            observations=observations,
        )


async def capture_pair(observation, store, *, client=None):
    """Archive a bound public pair; no model request or identity admission."""
    if client is None:
        async with httpx.AsyncClient(
            timeout=30,
            headers={
                "Referer": "https://www.cardhobby.com.cn/",
                "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/139 Safari/537.36",
            },
        ) as public_client:
            return await capture_pair(observation, store, client=public_client)
    sale, _ = validated_sale(observation)
    html_bytes = await read_bounded(client, sale.source_url, limit=2 * 1024 * 1024)
    html = html_bytes.decode("utf-8")
    now = datetime.now(timezone.utc)
    gallery = public_gallery_for_primary(
        parse_public_item_html(html, sale.source_url, now=now), sale.source_url, sale.image_url
    )
    if not gallery or len(gallery["image_urls"]) != 2:
        raise ValueError("price_pair_gallery_missing")
    primary = gallery_primary(gallery)
    urls = [primary, *[url for url in gallery["image_urls"] if url != primary]]
    images = []
    for url in urls:
        data = await read_bounded(client, url)
        validate_photo(data)
        with Image.open(BytesIO(data)) as image:
            mime = (
                "image/jpeg" if image.format in {"JPEG", "MPO"} else "image/" + image.format.lower()
            )
        images.append((url, data, mime))
    if images[0][1] == images[1][1]:
        raise ValueError("duplicate_price_images")
    store.put(html_bytes)
    for _, data, _ in images:
        store.put(data)
    return html, images, now


async def capture_receipt(observation, store, reader, *, client=None):
    html, images, now = await capture_pair(observation, store, client=client)
    try:
        result, usage = await reader.observe([(data, mime) for _, data, mime in images])
    except PriceImageReadError as error:
        error.trace_sha256 = store.put(
            json.dumps(
                dict(
                    contract="price-image-model-trace-v1",
                    model_calls=error.model_calls,
                    observations=error.observations,
                ),
                ensure_ascii=False,
            ).encode()
        )
        raise
    usage = dict(usage)
    if "observations" in usage:
        usage["observation_trace_sha256"] = store.put(
            json.dumps(
                dict(
                    contract="price-image-model-trace-v1",
                    model_calls=usage["model_calls"],
                    observations=usage.pop("observations"),
                ),
                ensure_ascii=False,
            ).encode()
        )
    return make_receipt(observation, html, images, result, model=reader.model, usage=usage, now=now)
