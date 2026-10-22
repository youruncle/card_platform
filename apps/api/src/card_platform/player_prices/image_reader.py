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
    PriceImageResult,
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
    face = dict(
        role="unknown",
        player_names=[],
        brand_name=None,
        product_name=None,
        insert_name=None,
        card_number=None,
        multi_subject=False,
        evidence=[],
    )
    physical = [
        dict(
            field=field,
            image_index=index,
            value=None,
            detail="需填写此图实际完整观察；布尔值用true/false/null，未知保留null",
        )
        for index in (0, 1)
        for field in (
            "card_count",
            "is_autograph",
            "is_memorabilia",
            "is_patch",
            "is_redemption",
            "is_numbered",
            "grade_status",
            "condition_status",
        )
    ]
    physical += [
        dict(
            field="card_number",
            image_index=1,
            value=None,
            number_role="unknown",
            detail="独立背面实际编号及具体印刷位置，角色不明保留unknown",
        ),
        dict(
            field="parallel_name",
            image_index=0,
            value=None,
            detail="独立正面实际颜色、纹理与表面印刷特征，不能以标题替代",
        ),
    ]
    example = dict(
        player_name=None,
        brand_name=None,
        product_name=None,
        card_number=None,
        insert_name=None,
        parallel_name=None,
        season_start=None,
        season_end=None,
        is_autograph=None,
        is_memorabilia=None,
        is_patch=None,
        is_redemption=None,
        is_numbered=None,
        serial_number=None,
        serial_total=None,
        grade_company=None,
        grade_value=None,
        grade_cert_number=None,
        grade_status="unknown",
        front_complete=False,
        back_complete=False,
        card_counts=[None, None],
        condition_status="uncertain",
        confidence=0,
        caveats=[],
        pair_validation=dict(status="unknown", reason_codes=[], evidence=[], front=face, back=face),
        physical_evidence=physical,
    )
    return (
        "核验两张公开拍卖商品图片的实际卡片身份。只读图片，不提供也不能猜卖家标题或已知卡款。"
        "严格输出此结构JSON，各字段无法确认填null/unknown；示例不是卡片事实："
        + json.dumps(example, ensure_ascii=False)
        + "。pair_validation.front为第0张的独立观察，back为第1张的独立观察；role写实际front/back，"
        "不能按顺序猜面。各面独立读姓名/厂商/产品/卡号，另一图缺失的值不得复制。"
        "card_counts按图片0/1分别记录实际卡片张数，无法数清为null；逐图给card_count证据。"
        "同球员多张也必须计数，多人同卡则multi_subject=true，并完整列出所有主体姓名。"
        "front_back_consistent不是输出字段，用pair_validation.status consistent/conflict/unknown；"
        "需两面实际主体/产品一致且确实一正一反，否则unknown/conflict。"
        "physical_evidence逐字段列field/image_index/value/detail；value必须与对应主字段一致，"
        '布尔value必须JSON true/false/null，不能写字符串"false"；card_count用整数。'
        "image_index只能0或1，不能用1/2；每个detail至少8个字符并写实际位置与可见内容。"
        "detail写具体位置、可见文字或图案，每个条目只引用实际上传的那张图。"
        "示例列出的每面8个观察字段都必须逐项回答，不可遗漏；未知写null及未能读取的具体原因。"
        "card_number必须有背面独立编号，不能取球衣号码、统计、卖家贴纸、版权生产码或限编分数。"
        "card_number证据必须逐字转写实际编号，并写number_role=collector_number；其他数字角色"
        "如serial_number/jersey/statistics/production_code必须保留实际角色，不得改称卡号。"
        "parallel_name必须由正面颜色和固定纹理证据建立，区分普通Base、光滑银折、碎冰、波纹、"
        "红白蓝/黑金等复合颜色；不得因出现Base字样就忽略其他表面。"
        "Topps Chrome背面REFRACTOR需结合正面非特定颜色光滑彩虹反射；Prizm背面单独PRIZM不能"
        "证明银折或任何具体版本。普通Base也是有效的表面结论，不必卡上印着Base才可判断；"
        "需给普通版实际版式与表面依据，不能把平滑银色印刷边框本身当作银折。"
        "仅当当前清楚可见的颜色/固定纹理/背标或评级标签仍无法区分具体版本时parallel_name=null；"
        "不要因理论上还可能存在未见版本就忽略已经清晰识别的表面特征。"
        "不能以某编号常见或官方可能有某种平行来推断本件版本。"
        "is_numbered在任意一面明确有机器x/y烫印才true并抄完整serial_number/serial_total；"
        "physical_evidence在烫印实际所在面给true，另一面没有则如实false/看不清null，不复制true。"
        "看清完整两面且均无印章主字段才false；无法确认有无则null。serial字段detail须含实际x/y。"
        "有无编号依当前两面清楚可见的完整表面判断，不要求保证看不到的暗部绝无印章；"
        "真实反光遮住可能印章的区域则保持null。"
        "grade_status为raw/graded/unknown；raw需两张完整平面裸卡且没有评级硬壳标签，逐面给依据；"
        "graded需实际标签上的grade_company、grade_value、grade_cert_number，分别记录标签原文位置，"
        "不能把标价、球员分数或卡套贴纸当评级。"
        "is_autograph/is_memorabilia/is_patch/is_redemption独立给出true/false/null；若可明确排除，"
        "每面分别给physical_evidence的false及具体依据，不能因物料false就自动填patch=false。"
        "这些主字段一面明确可见即可true，只在实际可见面给true证据；另一面缺失不能复制正面事实。"
        "正反面有真实缺失、遮挡或不完整时相应complete=false；完整面才true。"
        "condition_status=ordinary_visible表示两面实际可见部位适合普通成交比较，不是无暗伤或高分保证。"
        "图片清楚且未见明显损伤可填ordinary_visible，逐面记录实际边角/表面观察；"
        "明显折痕破损为material_damage；主要部位看不清或遮挡为uncertain。不能声明裸卡10分。"
        "season_start/end只在图片有产品发行行或评级标签明确赛季时填写，否则null；"
        "填写年份必须完整四位整数，赛季20-21须写2020/2021。"
        "版权年、大学/职业统计、履历和选秀年份不是发行年。"
        "insert_name仅卡内具体特卡，不把球员名字/常规履历栏目/产品Logo当特卡。"
        "只输出JSON；不输出价格、估值、搜索词或任何来自常识补全的字段。"
    )


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
        if len(images) != 2:
            raise ValueError("price_pair_images_required")
        content = [dict(type="text", text=image_prompt())]
        for index, (data, mime) in enumerate(images):
            validate_photo(data)
            if mime not in {"image/jpeg", "image/png", "image/webp"}:
                raise ValueError("invalid_price_image_type")
            content.append(
                dict(
                    type="text",
                    text=f"以下这张图片的 image_index={index}；所有引用此图的证据必须用此索引。",
                )
            )
            content.append(
                dict(
                    type="image_url",
                    image_url=dict(url=f"data:{mime};base64," + base64.b64encode(data).decode()),
                )
            )
        response = await traced_completion(
            self.client.chat.completions.create,
            stage="player_price_identity",
            model=self.model,
            messages=[
                dict(role="system", content="只读取实际上传图片中的球星卡信息，保持未知与冲突。"),
                dict(role="user", content=content),
            ],
            response_format={"type": "json_object"},
            max_tokens=4000,
            extra_body=nonthinking_extra_body(self.model),
        )
        raw = response.choices[0].message.content
        if not raw:
            raise ValueError("price_vision_response_empty")
        result = PriceImageResult.model_validate_json(raw)
        usage = response.usage
        return result, dict(
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )


async def capture_receipt(observation, store, reader, *, client=None):
    if client is None:
        async with httpx.AsyncClient(
            timeout=30,
            headers={
                "Referer": "https://www.cardhobby.com.cn/",
                "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/139 Safari/537.36",
            },
        ) as public_client:
            return await capture_receipt(observation, store, reader, client=public_client)
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
    result, usage = await reader.observe([(data, mime) for _, data, mime in images])
    return make_receipt(observation, html, images, result, model=reader.model, usage=usage, now=now)
