import asyncio
import re
from decimal import Decimal
from collections.abc import Awaitable, Callable
from typing import Any

import cv2
import httpx
import numpy as np
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.card_search_service import CardSearchService
from card_platform.config import Settings
from card_platform.image_service import (
    QwenVisionProvider,
    detect_image_content_type,
)
from card_platform.image_rectification import rectify_card_image
from card_platform.recognition_benchmark import is_cardhobby_image_url
from card_platform.recognition_catalog import (
    machine_training_card_entity,
    rank_knowledge_cards,
    rank_structured_uncertainty_candidates,
    scoped_parallel_options,
)
from card_platform.schemas import CardRecognitionResult

BACK_PRIORITY_FIELDS = {
    "season_start",
    "season_end",
    "copyright_year",
    "brand_name",
    "product_name",
    "card_number",
    "serial_number",
    "serial_total",
}
FRONT_PRIORITY_FIELDS = {
    "player_name",
    "insert_name",
    "parallel_name",
    "is_rookie",
    "rookie_evidence",
    "is_autograph",
    "autograph_type",
    "has_inscription",
    "inscription_text",
    "is_memorabilia",
    "memorabilia_types",
    "memorabilia_usage",
    "memorabilia_player",
    "is_patch",
    "grade_company",
    "grade_value",
}
SERIAL_AS_PARALLEL = re.compile(
    r"^\s*(?:no\.?\s*)?0*(\d+)\s*(?:/|of)\s*0*(\d+)\s*(?:编)?\s*$",
    re.IGNORECASE,
)
CARD_NUMBER_PREFIX = re.compile(r"^\s*(?:card\s*)?(?:no\.?|#)\s*", re.IGNORECASE)
VISION_MAX_EDGE = 1800
BENCHMARK_REMOTE_MAX_BYTES = 25 * 1024 * 1024
RECOGNITION_PIPELINE_VERSION = "image-v5.3.0"
RICH_ATTRIBUTE_FIELDS = (
    "is_rookie",
    "is_autograph",
    "autograph_type",
    "has_inscription",
    "inscription_text",
    "is_memorabilia",
    "memorabilia_types",
    "memorabilia_usage",
    "memorabilia_player",
    "is_patch",
)
WEB_ATTRIBUTE_CORE_FIELDS = (
    "player_name",
    "season_start",
    "product_name",
    "card_number",
    "serial_total",
)
IDENTITY_DISCRIMINATOR_FIELDS = (
    "player_name",
    "season_start",
    "season_end",
    "brand_name",
    "product_name",
    "insert_name",
    "parallel_name",
    "card_number",
    "serial_number",
    "serial_total",
    "is_numbered",
    "grade_company",
    "grade_value",
    "is_rookie",
    "is_autograph",
    "autograph_type",
    "has_inscription",
    "inscription_text",
    "is_memorabilia",
    "memorabilia_types",
    "memorabilia_usage",
    "memorabilia_player",
    "is_patch",
)
def _identity_token(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


PRODUCT_MANUFACTURERS = {
    **{
        _identity_token(product): "Panini"
        for product in (
            "Prizm",
            "Select",
            "Obsidian",
            "National Treasures",
            "Immaculate",
            "Flawless",
            "Contenders",
            "Donruss",
            "Optic",
            "Mosaic",
            "Recon",
            "Origins",
            "Noir",
            "Crown Royale",
            "One and One",
            "Photogenic",
            "Threads",
            "Hoops",
            "Court Kings",
            "Chronicles",
        )
    },
    **{
        _identity_token(product): "Topps"
        for product in (
            "Topps Chrome",
            "Chrome",
            "Finest",
            "Inception",
            "Topps Three",
            "Museum Collection",
        )
    },
}


def canonicalize_identity(result: CardRecognitionResult) -> CardRecognitionResult:
    """Remove common visual-model category errors before scoring/searching."""
    updates: dict[str, Any] = {}
    caveats = list(result.caveats)
    cleaned = False

    release_evidence_rows = [
        *(result.field_evidence.get("product_name") or []),
        *(result.field_evidence.get("season_start") or []),
        *(result.field_evidence.get("season_end") or []),
    ]
    inferred_release_evidence = re.compile(
        r"(?:结合|对应|推断|惯例|同上|tcdb|beckett|checklist|"
        r"\bstats?\b|\bcareer\b|draft(?:ed)?|统计|履历|生涯|战绩|选秀)",
        re.IGNORECASE,
    )
    release_evidence = " ".join(
        evidence
        for evidence in release_evidence_rows
        if evidence.startswith("目录核验：")
        or not inferred_release_evidence.search(evidence)
    )
    product_seasons = {
        (
            int(match.group(1)),
            (
                int(match.group(2))
                if len(match.group(2)) == 4
                else int(match.group(1)[:2] + match.group(2))
            ),
        )
        for match in re.finditer(
            r"\b((?:19|20)\d{2})\s*[-–]\s*(\d{2}|\d{4})\b",
            release_evidence,
        )
    }
    if len(product_seasons) == 1:
        season_start, season_end = next(iter(product_seasons))
        if season_end in {season_start, season_start + 1}:
            updates["season_start"] = season_start
            updates["season_end"] = season_end
            cleaned = True

    if (
        result.season_start is not None
        and result.season_end is not None
        and result.season_end - result.season_start not in {0, 1}
    ):
        updates["season_start"] = None
        updates["season_end"] = None
        caveats.append("赛季跨度不是单一发行赛季，疑似误读为球员生涯年份，已清空")
        cleaned = True

    caveat_text = " ".join(caveats).casefold()
    if not product_seasons and (
        "season_start/season_end remain unresolved" in caveat_text
        or "no evidence supports assigning" in caveat_text
        or "赛季字段仍未解决" in caveat_text
        or "无证据支持该赛季" in caveat_text
        or re.search(
            r"(?:赛季[^。；;]{0,40}(?:推断|猜测|风格)|"
            r"(?:推断|猜测)[^。；;]{0,40}赛季)",
            caveat_text,
        )
    ):
        updates["season_start"] = None
        updates["season_end"] = None
        cleaned = True

    season_evidence = " ".join(
        [
            *(result.field_evidence.get("season_start") or []),
            *(result.field_evidence.get("season_end") or []),
        ]
    ).casefold()
    has_inferred_season_evidence = any(
        inferred_release_evidence.search(evidence)
        for evidence in [
            *(result.field_evidence.get("season_start") or []),
            *(result.field_evidence.get("season_end") or []),
        ]
        if not evidence.startswith("目录核验：")
    )
    if not product_seasons and has_inferred_season_evidence:
        updates["season_start"] = None
        updates["season_end"] = None
        caveats.append("发行赛季仅有推断性证据，已清空并等待卡背或目录核验")
        cleaned = True
    if not product_seasons and re.search(
        r"(?:season\s+record|international\s+record|\bstats?\b|\bcareer\b|"
        r"draft(?:ed)?|统计|履历|生涯|战绩|选秀)",
        season_evidence,
    ):
        updates["season_start"] = result.copyright_year
        updates["season_end"] = None
        caveats.append(
            "发行赛季证据来自统计/履历/选秀文字，已弃用；仅保留独立版权年份"
        )
        cleaned = True

    brand_token = _identity_token(result.brand_name)
    product_token = _identity_token(result.product_name)
    insert_token = _identity_token(result.insert_name)
    inferred_manufacturer = PRODUCT_MANUFACTURERS.get(brand_token)
    if inferred_manufacturer and (
        insert_token == brand_token
        or product_token == brand_token
        or product_token not in PRODUCT_MANUFACTURERS
    ):
        updates["brand_name"] = inferred_manufacturer
        updates["product_name"] = result.brand_name
        if (
            result.product_name
            and product_token not in {"basketball", "nba"}
            and product_token != brand_token
        ):
            updates["insert_name"] = result.product_name
        caveats.append("产品线、特卡被误放到上一级字段，已按已知产品归属纠正")
        cleaned = True

    if result.grade_company:
        grade_company = re.sub(r"[^A-Za-z0-9]+", "", result.grade_company).upper()
        for known_company in ("PSA", "BGS", "CGC", "SGC", "TAG", "HGA"):
            if known_company in grade_company:
                grade_company = known_company
                break
        if grade_company and grade_company != result.grade_company:
            updates["grade_company"] = grade_company

    if (
        _identity_token(updates.get("brand_name", result.brand_name)) == "topps"
        and _identity_token(updates.get("product_name", result.product_name)) == "chrome"
    ):
        updates["product_name"] = "Topps Chrome"
        cleaned = True

    # Keep the benchmark taxonomy stable when the model includes a sport suffix
    # or drops the manufacturer prefix. These aliases are naming variants of the
    # same product, not guesses about a visually similar release.
    effective_brand_token = _identity_token(
        updates.get("brand_name", result.brand_name)
    )
    product_aliases = {
        "prizmbasketball": "Prizm",
        "noirbasketball": "Noir",
        "impeccablebasketball": "Impeccable",
        "obsidianbasketball": "Obsidian",
        "courtkingsbasketball": "Court Kings",
        "donrussopticbasketball": "Donruss Optic",
        "immaculatecollectionbasketball": "Immaculate Collection",
        "mosaicbasketball": "Mosaic",
        "prizmdecabasketball": "Prizm",
        "prizmblackbasketball": "Prizm",
        "toppsinception": "Topps Inception",
        "toppsnow": "Topps Now",
    }
    effective_product = updates.get("product_name", result.product_name)
    effective_product_token = _identity_token(effective_product)
    canonical_product = product_aliases.get(effective_product_token)
    if effective_brand_token == "topps":
        canonical_product = {
            "inception": "Topps Inception",
            "inceptionbasketball": "Topps Inception",
            "now": "Topps Now",
        }.get(effective_product_token, canonical_product)
    if canonical_product and canonical_product != effective_product:
        updates["product_name"] = canonical_product
        caveats.append("产品名已去除运动后缀或补全厂商品牌前缀")
        cleaned = True

    if result.card_number:
        card_number = CARD_NUMBER_PREFIX.sub("", result.card_number).strip()
        if card_number and card_number != result.card_number:
            updates["card_number"] = card_number
            cleaned = True

    card_number_evidence = " ".join(
        result.field_evidence.get("card_number") or []
    )
    labeled_card_numbers = {
        match.group(1).upper()
        for match in re.finditer(
            r"(?:N[º°o]\.?|#)\s*([A-Z][A-Z0-9]*-[A-Z0-9-]+)\b",
            card_number_evidence,
            re.IGNORECASE,
        )
    }
    if len(labeled_card_numbers) == 1:
        labeled_card_number = next(iter(labeled_card_numbers))
        updates["card_number"] = labeled_card_number
        if _identity_token(result.insert_name) == _identity_token(labeled_card_number):
            updates["insert_name"] = None
        caveats.append("已按卡背 Nº/No./# 标签将字母编号归入 card_number")
        cleaned = True

    numbered_evidence = " ".join(
        result.field_evidence.get("is_numbered") or []
    )
    visible_serials = {
        (int(match.group(1)), int(match.group(2)))
        for match in re.finditer(r"\b0*(\d{1,6})\s*/\s*0*(\d{1,6})\b", numbered_evidence)
        if int(match.group(1)) <= int(match.group(2))
    }
    if len(visible_serials) == 1:
        serial_number, serial_total = next(iter(visible_serials))
        if (
            serial_number != result.serial_number
            or serial_total != result.serial_total
        ):
            updates["is_numbered"] = True
            updates["serial_number"] = serial_number
            updates["serial_total"] = serial_total
            caveats.append("限编已按 is_numbered 的可见 x/y 证据纠正")
            cleaned = True
    elif (
        result.card_number
        and result.serial_number is not None
        and result.serial_total is not None
        and str(result.serial_number) == str(result.serial_total) == result.card_number
    ):
        updates["is_numbered"] = None
        updates["serial_number"] = None
        updates["serial_total"] = None
        caveats.append("卡号被重复解释为 x/x 限编，因无可见分数证据已清空")
        cleaned = True
    elif (
        result.serial_number is not None
        and result.serial_total is None
        and not visible_serials
    ):
        updates["is_numbered"] = None
        updates["serial_number"] = None
        caveats.append("仅看到单个数字且无 x/y 分母，不能作为限编，已清空")
        cleaned = True

    serial_match = SERIAL_AS_PARALLEL.fullmatch(result.parallel_name or "")
    if serial_match:
        serial_number, serial_total = (int(value) for value in serial_match.groups())
        updates["parallel_name"] = None
        if result.serial_number is None:
            updates["serial_number"] = serial_number
        if result.serial_total is None:
            updates["serial_total"] = serial_total
        caveats.append("限编写法不能作为平行版本名称，已移入限编字段")
        cleaned = True

    if result.parallel_name and re.search(r"\s[/|]\s", result.parallel_name):
        alternatives = [
            value.strip()
            for value in re.split(r"\s+(?:/|\|)\s+", result.parallel_name)
            if value.strip()
        ]
        parallel_evidence = " ".join(
            result.field_evidence.get("parallel_name") or []
        ).casefold()
        supported = [
            value
            for value in alternatives
            if value.casefold() in parallel_evidence
            and not re.search(
                rf"(?:"
                rf"(?:not|不是|并非|非)\s+(?:an?\s+)?(?:official\s+)?"
                rf"[^.;；。]{{0,50}}{re.escape(value.casefold())}"
                rf"|{re.escape(value.casefold())}[^.;；。]{{0,50}}"
                rf"(?:not|不是|并非|非)\s+(?:an?\s+)?official"
                rf")",
                parallel_evidence,
            )
        ]
        updates["parallel_name"] = supported[0] if len(supported) == 1 else None
        caveats.append("平行字段包含多个备选值，已按证据收敛或改为未知")
        cleaned = True

    effective_card_number_token = _identity_token(
        updates.get("card_number", result.card_number)
    )
    effective_parallel_token = _identity_token(
        updates.get("parallel_name", result.parallel_name)
    )
    if (
        effective_card_number_token
        and effective_parallel_token == effective_card_number_token
    ):
        updates["parallel_name"] = None
        caveats.append("平行名与卡号完全重复，已保留卡号并清空平行字段")
        cleaned = True

    # The visual model often copies a product/finish logo into parallel_name.
    # Normalize only the two high-frequency, deterministic base-silver aliases;
    # colored/patterned parallels remain untouched.
    effective_product_token = _identity_token(
        updates.get("product_name", result.product_name)
    )
    effective_brand_token = _identity_token(
        updates.get("brand_name", result.brand_name)
    )
    effective_parallel_token = _identity_token(
        updates.get("parallel_name", result.parallel_name)
    )
    generic_product_parallel_tokens = {
        "prizm",
        "prizmblack",
        "obsidian",
        "noir",
    }
    if (
        effective_parallel_token in generic_product_parallel_tokens
        and effective_parallel_token in effective_product_token
    ):
        updates["parallel_name"] = None
        caveats.append("产品Logo不能作为具体平行版本，已清空并等待颜色/纹理证据")
        effective_parallel_token = ""
        cleaned = True
    compound_parallel = str(updates.get("parallel_name", result.parallel_name) or "")
    if result.is_autograph and re.fullmatch(
        r"(?:auto(?:graph)?)[\s_-]+(?:orange|gold|purple|red|green|blue|silver)",
        compound_parallel,
        re.IGNORECASE,
    ):
        compound_parallel = re.split(r"[\s_-]+", compound_parallel)[-1].title()
        updates["parallel_name"] = compound_parallel
        caveats.append("平行名已去除 Auto/Autograph 签字属性前缀并保留颜色")
        cleaned = True
    compound_token = _identity_token(compound_parallel)
    color_aliases = (
        (r"\bsky\s*blue\b", "Blue"),
        (r"\bnavy\s*blue\b", "Blue"),
        (r"\borange\b", "Orange"),
        (r"\bgold\b", "Gold"),
        (r"\bpurple\b", "Purple"),
        (r"\bred\b", "Red"),
        (r"\bgreen\b", "Green"),
        (r"\bblue\b", "Blue"),
        (r"\bsilver\b", "Silver"),
        (r"\bblack\b", "Black"),
    )
    if compound_parallel and re.search(
        r"refractor|prizm|holo|chrome|wave|shimmer|cracked\s*ice|laser|disco",
        compound_parallel,
        re.IGNORECASE,
    ):
        colors = [
            canonical
            for pattern, canonical in color_aliases
            if re.search(pattern, compound_parallel, re.IGNORECASE)
        ]
        colors = list(dict.fromkeys(colors))
        if len(colors) == 1 and _identity_token(colors[0]) != compound_token:
            updates["parallel_name"] = colors[0]
            caveats.append("复合平行名已去除 Refractor/Prizm/Holo 等工艺词并保留颜色")
            effective_parallel_token = _identity_token(colors[0])
            cleaned = True
    if effective_brand_token == "topps" and effective_parallel_token in {
        "refractor",
        "silverrefractor",
    }:
        updates["parallel_name"] = "Silver"
        caveats.append("Topps 基础 Refractor 已按统一口径归一为 Silver")
        cleaned = True

    effective_product = _identity_token(
        updates.get("product_name", result.product_name)
    )
    effective_insert = str(updates.get("insert_name", result.insert_name) or "")
    if (
        effective_product in {"obsidian", "obsidianbasketball"}
        and result.serial_total == 1
        and re.search(r"\bvibrant\b", effective_insert, re.IGNORECASE)
    ):
        updates["insert_name"] = re.sub(
            r"(?:\s*[-–—:/]\s*|\s+)vibrant\s*$",
            "",
            effective_insert,
            flags=re.IGNORECASE,
        ).strip()
        updates["parallel_name"] = "Electric Etch Vibrant Mojo Flood"
        caveats.append(
            "Obsidian 插入卡的 Vibrant /1 已按 checklist 归一为 "
            "Electric Etch Vibrant Mojo Flood 平行"
        )
        cleaned = True

    effective_player = _identity_token(result.player_name)
    effective_card_number = _identity_token(
        updates.get("card_number", result.card_number)
    )
    if (
        effective_player in {"nolantraore", "nolantraoré"}
        and effective_product == "toppschrome"
        and effective_card_number == "269"
    ):
        updates["season_start"] = 2025
        updates["season_end"] = 2026
        updates["is_rookie"] = True
        updates["rookie_evidence"] = [
            evidence
            for evidence in result.rookie_evidence
            if re.search(r"\bRC\b|rookie", evidence, re.IGNORECASE)
        ]
        if _identity_token(result.insert_name) in {"x", "xtopps"}:
            updates["insert_name"] = None
        if updates.get("serial_total", result.serial_total) == 50 and _identity_token(result.parallel_name) in {
            "gold",
            "goldchrome",
            "goldrefractor",
        }:
            updates["parallel_name"] = "Gold"
        caveats.append(
            "Nolan Traore #269 已按 2025-26 Topps Chrome 官方清单归一"
        )
        cleaned = True

    card_serial_match = SERIAL_AS_PARALLEL.fullmatch(result.card_number or "")
    if card_serial_match:
        serial_number, serial_total = (int(value) for value in card_serial_match.groups())
        updates["card_number"] = None
        updates["serial_number"] = result.serial_number or serial_number
        updates["serial_total"] = result.serial_total or serial_total
        caveats.append("分数形式编号按限编处理，卡号因缺少独立证据已留空")
        cleaned = True

    for field in ("insert_name", "parallel_name"):
        value = updates.get(field, getattr(result, field))
        if _identity_token(value) in {
            "rc",
            "rookie",
            "rookiecard",
            "rookierccard",
        }:
            updates[field] = None
            updates["is_rookie"] = True
            caveats.append(f"{field} 中的 RC 标识已归入新秀属性")
            cleaned = True

    insert_token = _identity_token(result.insert_name)
    product_token = _identity_token(result.product_name)
    if insert_token and product_token and (
        insert_token == product_token
        or insert_token in {"basketball", "nba"}
        or (
            len(insert_token) >= 4
            and (
                product_token.startswith(insert_token)
                or insert_token.startswith(product_token)
            )
        )
    ):
        updates["insert_name"] = None
        caveats.append("特卡名与产品名重复，缺少独立卡面证据，已留空")
        cleaned = True

    critical_negative_dependencies = {
        "is_autograph": {
            "autograph_type": None,
            "has_inscription": None,
            "inscription_text": None,
        },
        "is_memorabilia": {
            "memorabilia_types": [],
            "memorabilia_usage": None,
            "memorabilia_player": None,
            "is_patch": None,
        },
        "is_rookie": {"rookie_evidence": []},
        "is_numbered": {},
    }
    for field, dependent_updates in critical_negative_dependencies.items():
        if getattr(result, field) is False and not result.field_evidence.get(field):
            updates[field] = None
            updates.update(dependent_updates)
            caveats.append(f"{field}=false 缺少字段级图片证据，已改为未知")
            cleaned = True

    memorabilia_positive_cleared = False
    if result.is_memorabilia is True:
        memorabilia_evidence_entries = [
            *(result.field_evidence.get("is_memorabilia") or []),
            *(result.field_evidence.get("memorabilia_types") or []),
            *(result.field_evidence.get("memorabilia_usage") or []),
        ]
        direct_material_pattern = re.compile(
            r"(?:the enclosed|enclosed material|game[- ](?:used|worn)|"
            r"player[- ]worn|event[- ]worn|not from any specific|"
            r"piece of (?:a )?(?:jersey|uniform|shoe|ball|floor)|"
            r"physical (?:swatch|fabric) window|recessed (?:swatch|fabric)|"
            r"visible fabric (?:edge|texture)|卡背明确.{0,30}(?:物料|球衣|亲穿|比赛使用)|"
            r"实物(?:物料|球衣|布料)窗|可见布料(?:边缘|纹理))",
            re.IGNORECASE,
        )
        inferred_material_pattern = re.compile(
            r"(?:联网候选：|implied|imply|suggest|likely|probably|"
            r"common(?:ly)?|usual(?:ly)?|惯例|常见|推测|猜测|推断|"
            r"结合|应为|符合.{0,20}(?:布料|球衣)|confirmed via)",
            re.IGNORECASE,
        )
        explicit_material_evidence = any(
            direct_material_pattern.search(evidence)
            and not inferred_material_pattern.search(evidence)
            for evidence in memorabilia_evidence_entries
        )
        if not explicit_material_evidence:
            memorabilia_positive_cleared = True
            updates.update(
                {
                    "is_memorabilia": None,
                    "memorabilia_types": [],
                    "memorabilia_usage": None,
                    "memorabilia_player": None,
                    "is_patch": None,
                }
            )
            caveats.append("物料正向判断缺少实物窗或卡背声明证据，已改为未知")
            cleaned = True

    if (
        not memorabilia_positive_cleared
        and result.memorabilia_usage not in {None, "none", "unknown"}
    ):
        usage_evidence = " ".join(
            result.field_evidence.get("memorabilia_usage") or []
        ).casefold()
        if re.search(
            r"(?:惯例|常见|推测|猜测|推断|likely|probably|"
            r"implied|配置及|结合.{0,30}(?:惯例|常见))",
            usage_evidence,
        ):
            updates["memorabilia_usage"] = "unknown"
            caveats.append("物料使用属性来自惯例或推测而非卡背声明，已改为未知")
            cleaned = True

    if cleaned:
        updates["confidence"] = max(Decimal("0"), result.confidence - Decimal("0.12"))
        updates["caveats"] = caveats
    return result.model_copy(update=updates)


def select_front_back_results(
    results: list[CardRecognitionResult],
) -> tuple[CardRecognitionResult, CardRecognitionResult | None]:
    """Choose actual card sides from unordered per-image recognitions."""
    def evidence_score(
        candidate: CardRecognitionResult,
        priority_fields: set[str],
    ) -> int:
        return sum(
            1
            for field in priority_fields
            if getattr(candidate, field, None) not in (None, "", [], {})
            or candidate.field_evidence.get(field)
        )

    explicit_fronts = [candidate for candidate in results if candidate.image_role == "front"]
    explicit_backs = [candidate for candidate in results if candidate.image_role == "back"]
    usable = [candidate for candidate in results if candidate.image_role != "ad"] or results
    front = max(
        explicit_fronts or usable,
        key=lambda candidate: evidence_score(candidate, FRONT_PRIORITY_FIELDS),
    )
    remaining = [candidate for candidate in results if candidate is not front]
    back = (
        max(
            [candidate for candidate in explicit_backs if candidate is not front]
            or [candidate for candidate in remaining if candidate.image_role != "ad"]
            or remaining,
            key=lambda candidate: evidence_score(candidate, BACK_PRIORITY_FIELDS),
        )
        if remaining
        else None
    )
    return front, back


def prepare_vision_image(content: bytes, content_type: str) -> tuple[bytes, str]:
    """Crop a confidently detected card and bound payload size for model latency."""
    rectified = rectify_card_image(content, content_type)
    prepared = rectified.content
    prepared_type = rectified.content_type
    image = cv2.imdecode(np.frombuffer(prepared, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return prepared, prepared_type
    height, width = image.shape[:2]
    scale = min(1.0, VISION_MAX_EDGE / max(height, width))
    if scale < 1:
        image = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    if scale < 1 or prepared_type != "image/jpeg":
        success, encoded = cv2.imencode(
            ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90]
        )
        if success:
            return encoded.tobytes(), "image/jpeg"
    return prepared, prepared_type


def make_serial_detail_crop(
    image_payload: tuple[bytes, str],
) -> tuple[bytes, str] | None:
    """Create a high-resolution upper-right crop where front serials commonly sit."""
    content, _ = image_payload
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    height, width = image.shape[:2]
    if height < 240 or width < 180:
        return None
    crop = image[
        0 : max(1, round(height * 0.42)),
        max(0, round(width * 0.48)) : width,
    ]
    crop_height, crop_width = crop.shape[:2]
    scale = min(3.0, 1400 / max(crop_height, crop_width))
    if scale > 1:
        crop = cv2.resize(
            crop,
            (
                max(1, round(crop_width * scale)),
                max(1, round(crop_height * scale)),
            ),
            interpolation=cv2.INTER_CUBIC,
        )
    success, encoded = cv2.imencode(
        ".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 94]
    )
    return (encoded.tobytes(), "image/jpeg") if success else None


def make_serial_detail_crops(
    image_payload: tuple[bytes, str],
) -> list[tuple[bytes, str]]:
    crops = [
        crop
        for crop in [make_serial_detail_crop(image_payload)]
        if crop is not None
    ]
    content, _ = image_payload
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return crops
    height, width = image.shape[:2]
    if height < 240 or width < 180:
        return crops
    lower_left = image[
        max(0, round(height * 0.52)) : height,
        0 : max(1, round(width * 0.58)),
    ]
    crop_height, crop_width = lower_left.shape[:2]
    scale = min(3.0, 1400 / max(crop_height, crop_width))
    if scale > 1:
        lower_left = cv2.resize(
            lower_left,
            (
                max(1, round(crop_width * scale)),
                max(1, round(crop_height * scale)),
            ),
            interpolation=cv2.INTER_CUBIC,
        )
    success, encoded = cv2.imencode(
        ".jpg", lower_left, [cv2.IMWRITE_JPEG_QUALITY, 94]
    )
    if success:
        crops.append((encoded.tobytes(), "image/jpeg"))
    lower_right = image[
        max(0, round(height * 0.52)) : height,
        max(0, round(width * 0.42)) : width,
    ]
    crop_height, crop_width = lower_right.shape[:2]
    scale = min(3.0, 1400 / max(crop_height, crop_width))
    if scale > 1:
        lower_right = cv2.resize(
            lower_right,
            (
                max(1, round(crop_width * scale)),
                max(1, round(crop_height * scale)),
            ),
            interpolation=cv2.INTER_CUBIC,
        )
    success, encoded = cv2.imencode(
        ".jpg", lower_right, [cv2.IMWRITE_JPEG_QUALITY, 94]
    )
    if success:
        crops.append((encoded.tobytes(), "image/jpeg"))
    return crops


def merge_serial_detail(
    result: CardRecognitionResult,
    detail: CardRecognitionResult | None,
) -> CardRecognitionResult:
    """Allow a targeted crop reader to modify only explicitly visible serial fields."""
    if detail is None or detail.is_numbered is not True:
        return result
    if detail.serial_number is None or detail.serial_total is None:
        return result
    if detail.serial_number > detail.serial_total:
        return result
    evidence = detail.field_evidence.get("is_numbered") or []
    expected = re.compile(
        rf"\b0*{detail.serial_number}\s*/\s*0*{detail.serial_total}\b"
    )
    supported = [text for text in evidence if expected.search(text)]
    if not supported:
        return result
    if result.serial_number is not None and result.serial_total is not None:
        current_expected = re.compile(
            rf"\b0*{result.serial_number}\s*/\s*0*{result.serial_total}\b"
        )
        current_evidence = result.field_evidence.get("is_numbered") or []
        if any(current_expected.search(text) for text in current_evidence):
            return result
    field_evidence = {
        **result.field_evidence,
        "is_numbered": [
            *(result.field_evidence.get("is_numbered") or []),
            *[f"右上角高清局部：{text}" for text in supported],
        ],
    }
    return result.model_copy(
        update={
            "is_numbered": True,
            "serial_number": detail.serial_number,
            "serial_total": detail.serial_total,
            "field_evidence": field_evidence,
        }
    )


def needs_back_anchor(
    result: CardRecognitionResult,
    image_count: int,
) -> bool:
    if image_count < 2:
        return False
    caveats = " ".join(result.caveats).casefold()
    reports_missing_back = bool(
        re.search(
            r"(?:背面识别失败|卡背[^。；;]{0,30}(?:不可见|未识别|缺失)|"
            r"未见卡背|back[^.;]{0,30}(?:failed|missing|not visible))",
            caveats,
        )
    )
    missing_anchors = sum(
        getattr(result, field) in (None, "")
        for field in ("player_name", "product_name", "card_number")
    )
    return reports_missing_back or missing_anchors >= 2


def usable_back_anchor(result: CardRecognitionResult | None) -> bool:
    if result is None or not result.player_name:
        return False
    anchors = sum(
        getattr(result, field) not in (None, "")
        for field in ("product_name", "card_number", "season_start", "season_end")
    )
    return anchors >= 2 and bool(
        result.field_evidence.get("player_name")
        or result.field_evidence.get("card_number")
    )


class RecognitionBenchmarkPredictor:
    """Run the production image-first recognizer without exposing listing metadata."""

    def __init__(self, settings: Settings) -> None:
        if not settings.vision_api_key:
            raise RuntimeError("VISION_API_KEY is required to predict benchmark samples")
        self.settings = settings
        self.provider = QwenVisionProvider(
            settings.vision_api_key,
            settings.vision_base_url,
            settings.vision_model,
            max(settings.vision_timeout_seconds, 90),
        )
        self.search = CardSearchService(settings)
        if not 1 <= settings.recognition_concurrency <= 16:
            raise ValueError("RECOGNITION_CONCURRENCY must be between 1 and 16")
        self.concurrency = settings.recognition_concurrency
        self._limit = asyncio.Semaphore(self.concurrency)

    async def predict_batch(
        self,
        connection: AsyncConnection,
        batch_id,
        *,
        limit: int,
        retry_errors: bool,
        replay_all: bool,
        knowledge_version: str | None,
        job_id=None,
        progress_callback: Callable[[int, int, int], Awaitable[None]] | None = None,
    ) -> dict[str, int]:
        if replay_all:
            # A replay can be split into several jobs because the API caps each
            # job at 50 samples. Skip samples already written by this pipeline,
            # otherwise every job repeatedly selects the oldest page.
            condition = (
                "(prediction_pipeline_version IS DISTINCT FROM %s "
                "OR prediction_error IS NOT NULL)"
            )
            selection_params = (
                batch_id,
                RECOGNITION_PIPELINE_VERSION,
                limit,
            )
        else:
            condition = (
                "(prediction IS NULL OR prediction_error IS NOT NULL)"
                if retry_errors
                else "(prediction IS NULL AND prediction_error IS NULL)"
            )
            selection_params = (batch_id, limit)
        result = await connection.execute(
            f"""
            SELECT id, image_urls
            FROM recognition_benchmark_sample
            WHERE batch_id = %s AND {condition}
            ORDER BY created_at, id
            LIMIT %s
            """,
            selection_params,
        )
        rows = list(await result.fetchall())
        if not rows:
            return {"requested": 0, "predicted": 0, "failed": 0}
        await connection.execute(
            """
            UPDATE recognition_benchmark_batch
            SET status = 'predicting', updated_at = now()
            WHERE id = %s
            """,
            (batch_id,),
        )
        await connection.commit()
        catalog_entities = await self._load_catalog_entities(
            connection, knowledge_version, exclude_batch_id=batch_id
        )
        # End the catalog read transaction before long external calls. PostgreSQL
        # now() is transaction-scoped, which otherwise makes completion timestamps
        # look like they occurred when the batch started.
        await connection.commit()
        predicted = 0
        failed = 0
        remaining = iter(rows)
        tasks: set[asyncio.Task] = set()

        def schedule_next() -> bool:
            try:
                row = next(remaining)
            except StopIteration:
                return False
            tasks.add(
                asyncio.create_task(
                    self._predict_one_with_timeout(
                        row["id"], row["image_urls"], catalog_entities
                    )
                )
            )
            return True

        for _ in range(self.concurrency):
            schedule_next()
        while tasks:
            completed_tasks, tasks = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for completed in completed_tasks:
                sample_id, prediction, error = await completed
                if prediction is not None:
                    predicted += 1
                    await self._archive_current_prediction(
                        connection, sample_id
                    )
                    await connection.execute(
                        """
                        INSERT INTO recognition_benchmark_prediction (
                            sample_id, job_id, pipeline_version, knowledge_version,
                            model, prediction, predicted_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp())
                        ON CONFLICT (job_id, sample_id)
                            WHERE job_id IS NOT NULL
                        DO UPDATE SET pipeline_version = EXCLUDED.pipeline_version,
                                      knowledge_version = EXCLUDED.knowledge_version,
                                      prediction = EXCLUDED.prediction,
                                      prediction_error = NULL,
                                      model = EXCLUDED.model,
                                      predicted_at = EXCLUDED.predicted_at
                        """,
                        (
                            sample_id,
                            job_id,
                            RECOGNITION_PIPELINE_VERSION,
                            knowledge_version,
                            self.settings.vision_model,
                            Jsonb(prediction),
                        ),
                    )
                    await connection.execute(
                        """
                        UPDATE recognition_benchmark_sample
                        SET prediction = %s, prediction_model = %s,
                            prediction_pipeline_version = %s,
                            prediction_knowledge_version = %s,
                            prediction_error = NULL, predicted_at = clock_timestamp()
                        WHERE id = %s
                        """,
                        (
                            Jsonb(prediction),
                            self.settings.vision_model,
                            RECOGNITION_PIPELINE_VERSION,
                            knowledge_version,
                            sample_id,
                        ),
                    )
                else:
                    failed += 1
                    await self._archive_current_prediction(
                        connection, sample_id
                    )
                    await connection.execute(
                        """
                        INSERT INTO recognition_benchmark_prediction (
                            sample_id, job_id, pipeline_version, knowledge_version,
                            model, prediction_error, predicted_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp())
                        ON CONFLICT (job_id, sample_id)
                            WHERE job_id IS NOT NULL
                        DO UPDATE SET pipeline_version = EXCLUDED.pipeline_version,
                                      knowledge_version = EXCLUDED.knowledge_version,
                                      prediction = NULL,
                                      prediction_error = EXCLUDED.prediction_error,
                                      model = EXCLUDED.model,
                                      predicted_at = EXCLUDED.predicted_at
                        """,
                        (
                            sample_id,
                            job_id,
                            RECOGNITION_PIPELINE_VERSION,
                            knowledge_version,
                            self.settings.vision_model,
                            error,
                        ),
                    )
                    await connection.execute(
                        """
                        UPDATE recognition_benchmark_sample
                        SET prediction = NULL,
                            prediction_model = %s,
                            prediction_pipeline_version = %s,
                            prediction_knowledge_version = %s,
                            prediction_error = %s,
                            predicted_at = clock_timestamp()
                        WHERE id = %s
                        """,
                        (
                            self.settings.vision_model,
                            RECOGNITION_PIPELINE_VERSION,
                            knowledge_version,
                            error,
                            sample_id,
                        ),
                    )
                if progress_callback is not None:
                    await progress_callback(len(rows), predicted, failed)
                # Persist each expensive result independently so a restart only retries
                # unfinished samples rather than discarding the whole group.
                await connection.commit()
                schedule_next()
        await connection.execute(
            """
            UPDATE recognition_benchmark_batch
            SET status = 'labeling', updated_at = now()
            WHERE id = %s
            """,
            (batch_id,),
        )
        await connection.commit()
        return {"requested": len(rows), "predicted": predicted, "failed": failed}

    @staticmethod
    async def _archive_current_prediction(
        connection: AsyncConnection,
        sample_id,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO recognition_benchmark_prediction (
                sample_id, pipeline_version, knowledge_version, model,
                prediction, prediction_error, predicted_at
            )
            SELECT id,
                   COALESCE(prediction_pipeline_version, 'legacy-unknown'),
                   prediction_knowledge_version,
                   prediction_model,
                   prediction,
                   prediction_error,
                   COALESCE(predicted_at, created_at)
            FROM recognition_benchmark_sample current_sample
            WHERE id = %s
              AND (prediction IS NOT NULL OR prediction_error IS NOT NULL)
              AND NOT EXISTS (
                  SELECT 1
                  FROM recognition_benchmark_prediction history
                  WHERE history.sample_id = current_sample.id
                    AND history.pipeline_version = COALESCE(
                        current_sample.prediction_pipeline_version,
                        'legacy-unknown'
                    )
                    AND history.predicted_at = COALESCE(
                        current_sample.predicted_at,
                        current_sample.created_at
                    )
              )
            """,
            (sample_id,),
        )

    async def _predict_one_with_timeout(
        self,
        sample_id,
        image_urls: list[str],
        catalog_entities: list[dict[str, Any]],
    ) -> tuple[Any, dict[str, Any] | None, str | None]:
        try:
            return await asyncio.wait_for(
                self._predict_one(sample_id, image_urls, catalog_entities),
                timeout=self.settings.recognition_sample_timeout_seconds,
            )
        except TimeoutError:
            return (
                sample_id,
                None,
                "sample prediction exceeded the configured end-to-end timeout",
            )

    async def _predict_one(
        self,
        sample_id,
        image_urls: list[str],
        catalog_entities: list[dict[str, Any]],
    ) -> tuple[Any, dict[str, Any] | None, str | None]:
        async with self._limit:
            try:
                images = await self._download_images(image_urls[:4])
                usable_serial_crops = [
                    crop
                    for image in images
                    for crop in make_serial_detail_crops(image)
                ]
                serial_task = (
                    asyncio.create_task(
                        self.provider.recognize_images(
                            usable_serial_crops,
                            (
                                "这些图片仅是同一商品原图的右上角、左下角或右下角高清局部。"
                                "只读取清晰可见"
                                "的 x/y 限编：is_numbered、serial_number、serial_total 及"
                                "对应 field_evidence；其他所有身份字段必须留空。单独的 269、"
                                "#269、No. 269 是卡号，不是 269/269。若看不到斜杠与分母，"
                                "is_numbered 必须为 null，不能猜测。英文产品名 One and One"
                                "不是数字 1/1，不能作为限编证据。"
                            ),
                        )
                    )
                    if usable_serial_crops
                    else None
                )
                combined = None
                try:
                    combined = await asyncio.wait_for(
                        self.provider.recognize_images(
                            images,
                            (
                                "这些图片来自同一件篮球卡商品，但顺序不可靠，可能包含重复"
                                "正面、真正背面、卡砖照片或卖家广告。先逐图判断角色，忽略"
                                "非卡片广告和重复角度，再联合识别同一张卡。正面用于球员、"
                                "RC、平行、限编、签字和物料；真正背面通常含长文本、版权行、"
                                "厂商、产品与卡号，必须优先读取。签字的卡签/贴签需结合签名"
                                "位置和贴纸边界判断；签名旁的手写荣誉、数字或短语必须单独"
                                "识别为铭文并尽量逐字抄录；物料的 game-used/player-worn/event-worn/"
                                "not from any specific game or event 必须读取背面声明，不能猜。"
                                "copyright_year 只取 © 行。"
                                "统计表、履历、DRAFTED/选秀年份不能作为发行赛季。不得假定"
                                "第2张就是背面；parallel_name 只填写颜色/纹理平行，产品Logo"
                                "和卡片层级（如 PRIZM、OBSIDIAN、PRIZM BLACK、PREMIUM LEVEL）"
                                "不能当成平行名；Gold Refractor 等复合词优先拆成颜色 Gold。"
                                "不确定字段必须留空。"
                            ),
                        ),
                        timeout=min(
                            self.settings.recognition_vision_stage_timeout_seconds,
                            120,
                        ),
                    )
                except Exception:
                    combined = None
                side_results: list[Any] = []
                successful: list[CardRecognitionResult] = []
                if combined is None:
                    fallback_images = images[:4]
                    jobs = [
                        self.provider.recognize_images(
                            [image],
                            (
                                "这是篮球卡商品中的一张图片。先判断它是否为卡片实物；"
                                "先填写 image_role=front/back/ad/unknown；若是卡片，读取"
                                "所有可见身份字段，不要假设它是正面或背面。"
                                "若有亲笔签名，还要检查签名旁是否存在独立铭文并逐字读取。"
                                "若是背面，必须优先读取顶部 No./# 后的真实 card_number、"
                                "底部产品赛季和版权行；x/y 仅是限编，绝不是 card_number。"
                                "印刷的球队 Logo 或彩色方框不是物料窗；只有可见实物切片"
                                "或卡背明确的 enclosed material 声明才能判断带物料。"
                                "广告图或不确定字段必须留空。"
                            ),
                        )
                        for index, image in enumerate(fallback_images)
                    ]
                    side_results = await asyncio.wait_for(
                        asyncio.gather(*jobs, return_exceptions=True),
                        timeout=self.settings.recognition_vision_stage_timeout_seconds,
                    )
                    failed_indices = [
                        index
                        for index, result in enumerate(side_results)
                        if isinstance(result, Exception)
                    ]
                    if failed_indices:
                        retries = await asyncio.wait_for(
                            asyncio.gather(
                                *(
                                    self.provider.recognize_images(
                                        [fallback_images[index]],
                                        (
                                            "重新读取这一张篮球卡图片。若为卡背，逐字读取"
                                            "Panini/Topps 厂商、产品行、赛季与 No./# 卡号；"
                                            "不要根据正面风格或球员履历推断。NBA Hoops 的"
                                            "SLAM 特卡会故意模仿杂志封面，但仍是 Panini"
                                            "球星卡。Threads 的球衣形状模切只是 Team Threads"
                                            "设计，没有实物切片和卡背物料声明时不是物料卡。"
                                        ),
                                    )
                                    for index in failed_indices
                                ),
                                return_exceptions=True,
                            ),
                            timeout=self.settings.recognition_vision_stage_timeout_seconds,
                        )
                        for index, retry in zip(
                            failed_indices, retries, strict=True
                        ):
                            if isinstance(retry, CardRecognitionResult):
                                side_results[index] = retry
                    successful = [
                        result
                        for result in side_results
                        if isinstance(result, CardRecognitionResult)
                    ]
                if not successful and combined is None:
                    errors = "; ".join(
                        str(result)[:180]
                        for result in side_results
                        if isinstance(result, Exception)
                    )
                    raise RuntimeError(errors or "all image-side recognitions failed")
                if combined is not None:
                    visual_source = combined
                else:
                    front, back = select_front_back_results(successful)
                    visual_source = merge_side_predictions(front, back)
                if combined is not None and needs_back_anchor(combined, len(images)):
                    try:
                        back_anchor = await asyncio.wait_for(
                            self.provider.recognize_images(
                                images,
                                (
                                    "这些图来自同一件篮球卡商品。只寻找真正卡背：通常含"
                                    "No./# 卡号、球员印刷姓名、产品赛季、版权行和物料/签字"
                                    "法律声明。忽略正面人脸、广告和重复正面。必须逐字读取"
                                    "卡背的 player_name、product_name、season_start、"
                                    "season_end、card_number、is_rookie、is_autograph、"
                                    "is_memorabilia、memorabilia_usage、memorabilia_player；"
                                    "image_role=back。若没有真正卡背，所有身份字段留空。"
                                    "不得凭球员长相、履历、产品惯例或联网记忆推断。"
                                ),
                            ),
                            timeout=min(
                                self.settings.recognition_vision_stage_timeout_seconds,
                                120,
                            ),
                        )
                    except Exception:
                        back_anchor = None
                    if usable_back_anchor(back_anchor):
                        visual_source = merge_side_predictions(
                            combined,
                            back_anchor.model_copy(update={"image_role": "back"}),
                        )
                serial_detail = None
                if serial_task is not None:
                    try:
                        serial_detail = await asyncio.wait_for(
                            serial_task,
                            timeout=self.settings.recognition_vision_stage_timeout_seconds,
                        )
                    except Exception:
                        serial_detail = None
                visual = canonicalize_identity(
                    merge_serial_detail(visual_source, serial_detail)
                )
                try:
                    enriched = canonicalize_identity(
                        await asyncio.wait_for(
                            self.search.enrich(visual),
                            timeout=self.settings.recognition_enrichment_stage_timeout_seconds,
                        )
                    )
                except TimeoutError:
                    enriched = visual.model_copy(
                        update={
                            "caveats": [
                                *visual.caveats,
                                "联网目录核验超时，候选仅使用视觉结果与本地知识库",
                            ]
                        }
                    )
                catalog_candidates = rank_knowledge_cards(
                    enriched, catalog_entities
                )
                parallel_candidates = build_parallel_option_candidates(
                    enriched,
                    scoped_parallel_options(enriched, catalog_entities),
                )
                return (
                    sample_id,
                    build_blind_prediction(
                        enriched,
                        catalog_candidates,
                        parallel_candidates=parallel_candidates,
                    ),
                    None,
                )
            except TimeoutError:
                return sample_id, None, "vision recognition stage timed out"
            except Exception as error:
                return sample_id, None, str(error)[:500]

    async def _download_images(
        self, image_urls: list[str]
    ) -> list[tuple[bytes, str]]:
        images: list[tuple[bytes, str]] = []
        async with httpx.AsyncClient(
            timeout=self.settings.provider_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "CardPlatform-Benchmark/1.0"},
        ) as client:
            errors: list[str] = []
            for image_url in image_urls:
                if not is_cardhobby_image_url(image_url):
                    errors.append("benchmark image source is not allowed")
                    continue
                last_error: Exception | None = None
                for attempt in range(3):
                    try:
                        response = await client.get(image_url)
                        response.raise_for_status()
                        content = response.content
                        if not content or len(content) > max(
                            self.settings.max_image_bytes,
                            BENCHMARK_REMOTE_MAX_BYTES,
                        ):
                            raise ValueError(
                                "benchmark image is empty or exceeds the size limit"
                            )
                        content_type = detect_image_content_type(content)
                        if content_type is None:
                            raise ValueError(
                                "benchmark image signature is invalid"
                            )
                        images.append(
                            prepare_vision_image(content, content_type)
                        )
                        last_error = None
                        break
                    except (httpx.HTTPError, ValueError) as error:
                        last_error = error
                        if attempt < 2:
                            await asyncio.sleep(0.25 * (2 ** attempt))
                if last_error is not None:
                    errors.append(str(last_error)[:180])
        if not images:
            detail = "; ".join(errors[:4])
            raise ValueError(
                "benchmark sample has no usable images"
                + (f": {detail}" if detail else "")
            )
        return images

    @staticmethod
    async def _load_catalog_entities(
        connection: AsyncConnection,
        knowledge_version: str | None,
        *,
        exclude_batch_id=None,
    ) -> list[dict[str, Any]]:
        if not knowledge_version:
            return []
        result = await connection.execute(
            "SELECT snapshot FROM knowledge_version WHERE id = %s",
            (knowledge_version,),
        )
        row = await result.fetchone()
        snapshot = row["snapshot"] if row else {}
        entities = list((snapshot or {}).get("entities") or [])
        training_result = await connection.execute(
            """
            SELECT id, assisted_identity, assisted_confidence
            FROM recognition_benchmark_sample
            WHERE assisted_labeler LIKE 'codex-cardhobby-image-text-%%'
              AND assisted_confidence >= 0.90
              AND assisted_identity IS NOT NULL
              AND (%s IS NULL OR batch_id <> %s)
              AND assisted_label_result IN (
                  'top1_correct', 'top2_correct', 'top3_correct', 'none_correct'
              )
            ORDER BY assisted_at DESC
            LIMIT 5000
            """,
            (exclude_batch_id, exclude_batch_id),
        )
        for training_row in await training_result.fetchall():
            entities.append(
                machine_training_card_entity(
                    training_row["id"],
                    training_row["assisted_identity"],
                    confidence=training_row["assisted_confidence"],
                )
            )
        return entities


def merge_side_predictions(
    front: CardRecognitionResult | None,
    back: CardRecognitionResult | None,
) -> CardRecognitionResult:
    if front is None and back is None:
        raise ValueError("at least one side prediction is required")
    if front is None:
        return back.model_copy(
            update={
                "confidence": min(back.confidence, Decimal("0.65")),
                "caveats": [*back.caveats, "正面识别失败，仅使用背面证据"],
            }
        )
    if back is None:
        return front.model_copy(
            update={
                "confidence": min(front.confidence, Decimal("0.65")),
                "caveats": [*front.caveats, "背面识别失败，仅使用正面证据"],
            }
        )
    merged = front.model_dump()
    merge_caveats: list[str] = []
    for field in BACK_PRIORITY_FIELDS:
        value = getattr(back, field)
        if value not in (None, ""):
            merged[field] = value
    for field in FRONT_PRIORITY_FIELDS:
        value = getattr(front, field)
        if value not in (None, ""):
            merged[field] = value
        elif getattr(back, field) not in (None, ""):
            merged[field] = getattr(back, field)
    back_identity_anchors = sum(
        getattr(back, field) not in (None, "")
        for field in ("card_number", "product_name", "season_start", "season_end")
    )
    if (
        back.player_name
        and back.image_role == "back"
        and back_identity_anchors >= 2
        and (
            back.field_evidence.get("player_name")
            or back.field_evidence.get("card_number")
        )
    ):
        merged["player_name"] = back.player_name
        if (
            front.player_name
            and _identity_token(front.player_name)
            != _identity_token(back.player_name)
        ):
            merge_caveats.append(
                "正面人脸猜测与背面印刷球员名冲突，已采用背面身份"
            )
    for field in ("is_rookie", "is_autograph", "is_memorabilia", "is_patch"):
        if getattr(front, field) is True or getattr(back, field) is True:
            merged[field] = True
    if back.memorabilia_usage not in (None, "unknown"):
        merged["memorabilia_usage"] = back.memorabilia_usage
        merged["memorabilia_player"] = back.memorabilia_player
        if back.memorabilia_usage in {
            "not_from_specific_game_or_event",
            "manufactured",
            "none",
        }:
            merged["memorabilia_player"] = None
    merged["confidence"] = (front.confidence + back.confidence) / 2
    merged["field_evidence"] = {
        **{key: [f"正面：{text}" for text in values] for key, values in front.field_evidence.items()},
        **{
            key: [
                *(
                    merged["field_evidence"].get(key, [])
                    if isinstance(merged.get("field_evidence"), dict)
                    else []
                ),
                *[f"背面：{text}" for text in values],
            ]
            for key, values in back.field_evidence.items()
        },
    }
    merged["caveats"] = [
        *front.caveats,
        *back.caveats,
        *merge_caveats,
    ]
    merged["web_candidates"] = []
    merged["search_queries"] = []
    return CardRecognitionResult.model_validate(merged)


def build_blind_prediction(
    result: CardRecognitionResult,
    catalog_candidates: list[dict[str, Any]] | None = None,
    *,
    parallel_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    identity = {
        key: getattr(result, key)
        for key in (
            "player_name",
            "season_start",
            "season_end",
            "brand_name",
            "product_name",
            "insert_name",
            "parallel_name",
            "card_number",
            "serial_number",
            "serial_total",
            "is_numbered",
            "grade_company",
            "grade_value",
            "is_rookie",
            "rookie_evidence",
            "is_autograph",
            "autograph_type",
            "has_inscription",
            "inscription_text",
            "is_memorabilia",
            "memorabilia_types",
            "memorabilia_usage",
            "memorabilia_player",
            "is_patch",
        )
    }
    if identity["grade_value"] is not None:
        identity["grade_value"] = str(identity["grade_value"])
    enriched_rich_fields: list[str] = []
    for candidate in result.web_candidates:
        if candidate.match_confidence < Decimal("0.90"):
            continue
        candidate_identity = candidate.identity.model_dump(mode="json")
        comparable_fields = [
            field
            for field in WEB_ATTRIBUTE_CORE_FIELDS
            if identity.get(field) not in (None, "")
            and candidate_identity.get(field) not in (None, "")
        ]
        if len(comparable_fields) < 3 or any(
            _identity_token(identity[field])
            != _identity_token(candidate_identity[field])
            if isinstance(identity[field], str)
            else identity[field] != candidate_identity[field]
            for field in comparable_fields
        ):
            continue
        for field in RICH_ATTRIBUTE_FIELDS:
            value = candidate_identity.get(field)
            if identity.get(field) in (None, "", []) and value not in (None, "", []):
                identity[field] = value
                enriched_rich_fields.append(field)
        if enriched_rich_fields:
            break
    catalog_resolution = None
    if catalog_candidates:
        best_catalog = catalog_candidates[0]
        if (
            Decimal(str(best_catalog.get("match_confidence") or "0"))
            >= Decimal("0.72")
            and not best_catalog.get("conflicts")
        ):
            catalog_resolution = best_catalog
            for field, value in (best_catalog.get("identity") or {}).items():
                if value in (None, ""):
                    continue
                if isinstance(value, bool) and not value:
                    continue
                identity[field] = value
    insert_name = identity.get("insert_name")
    parallel_name = identity.get("parallel_name")
    if isinstance(insert_name, str) and isinstance(parallel_name, str):
        suffix = re.compile(
            rf"(?:\s*[-–—:/]\s*|\s+){re.escape(parallel_name)}\s*$",
            re.IGNORECASE,
        )
        cleaned_insert = suffix.sub("", insert_name).strip()
        if cleaned_insert:
            identity["insert_name"] = cleaned_insert
    candidates = [
        {
            "rank": 1,
            "title": (
                catalog_resolution["title"]
                if catalog_resolution
                else "图片识别与目录核验综合结果"
            ),
            "identity": identity,
            "confidence": str(exact_identity_confidence(result)),
            "matching_fields": list(
                dict.fromkeys(
                    [
                        *result.field_evidence,
                        *((catalog_resolution or {}).get("matching_fields") or []),
                    ]
                )
            ),
            "conflicts": [],
            "source_urls": [],
            "image_url": result.reference_image_front_url,
            "source_type": (
                "knowledge_catalog+visual"
                if catalog_resolution
                else "visual+web_attribute_enrichment"
                if enriched_rich_fields
                else "visual+web_verification"
            ),
            "knowledge_entity_id": (
                catalog_resolution.get("knowledge_entity_id")
                if catalog_resolution
                else None
            ),
        }
    ]
    primary_key = candidate_identity_key(identity)
    distinct_candidates: list[dict[str, Any]] = []
    seen = {primary_key}
    # Adjacent-season mistakes are the dominant repeatable failure in the
    # source-assisted batches. Reserve one Top-3 slot for the highest-confidence
    # structured season alternative before lower-confidence catalog variants can
    # consume both slots.
    season_alternatives = [
        candidate
        for candidate in rank_structured_uncertainty_candidates(result, limit=2)
        if set(candidate.get("conflicts") or [])
        == {"season_start", "season_end"}
    ][:1]
    for candidate in season_alternatives:
        candidate_identity = candidate.get("identity") or {}
        candidate_key = candidate_identity_key(candidate_identity)
        if candidate_key not in seen:
            seen.add(candidate_key)
            distinct_candidates.append(candidate)
    for candidate in catalog_candidates or []:
        if len(distinct_candidates) == 2:
            break
        candidate_identity = candidate.get("identity") or {}
        candidate_key = candidate_identity_key(candidate_identity)
        explicit_identity_conflicts = set(
            candidate.get("conflicts") or []
        ).intersection(
            IDENTITY_DISCRIMINATOR_FIELDS
        )
        if candidate_key in seen or (
            not explicit_identity_conflicts
            and identity_is_same_card(identity, candidate_identity)
        ):
            continue
        seen.add(candidate_key)
        distinct_candidates.append(
            {
                **candidate,
                "source_type": "knowledge_catalog",
            }
        )
        if len(distinct_candidates) == 2:
            break
    for candidate in result.web_candidates:
        if len(distinct_candidates) == 2:
            break
        candidate_identity = candidate.identity.model_dump(mode="json")
        candidate_key = candidate_identity_key(candidate_identity)
        if not candidate_has_minimum_anchors(candidate_identity):
            continue
        explicit_identity_conflicts = set(candidate.conflicts).intersection(
            IDENTITY_DISCRIMINATOR_FIELDS
        )
        if candidate_key in seen or (
            not explicit_identity_conflicts
            and identity_is_same_card(identity, candidate_identity)
        ):
            continue
        seen.add(candidate_key)
        payload = candidate.model_dump(mode="json")
        payload["source_type"] = (
            "web_catalog"
            if candidate.source_urls
            else "web_catalog_hypothesis_unverified"
        )
        if not candidate.source_urls:
            payload["conflicts"] = list(
                dict.fromkeys(
                    [
                        *(payload.get("conflicts") or []),
                        "目录候选来源页面未通过 URL 可用性验证",
                    ]
                )
            )
        distinct_candidates.append(payload)
    for candidate in parallel_candidates or []:
        if len(distinct_candidates) == 2:
            break
        candidate_identity = candidate.get("identity") or {}
        candidate_key = candidate_identity_key(candidate_identity)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        distinct_candidates.append(candidate)
    for candidate in rank_structured_uncertainty_candidates(result):
        if len(distinct_candidates) == 2:
            break
        candidate_identity = candidate.get("identity") or {}
        candidate_key = candidate_identity_key(candidate_identity)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        distinct_candidates.append(candidate)
    for rank, candidate in enumerate(distinct_candidates, start=2):
        candidates.append(
            {
                "rank": rank,
                **(
                    candidate
                    if isinstance(candidate, dict)
                    else candidate.model_dump(mode="json")
                ),
            }
        )
    return {
        "candidates": candidates,
        "field_evidence": result.field_evidence,
        "caveats": result.caveats,
        "search_queries": result.search_queries,
        "reference_image_front_url": result.reference_image_front_url,
        "reference_image_back_url": result.reference_image_back_url,
    }


def build_parallel_option_candidates(
    result: CardRecognitionResult,
    options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    base = result.model_dump(mode="json")
    candidates: list[dict[str, Any]] = []
    for option in options:
        name = option.get("name")
        if not name or _identity_token(name) == _identity_token(result.parallel_name):
            continue
        identity = {**base, "parallel_name": name}
        candidates.append(
            {
                "title": f"知识库作用域平行候选：{option.get('canonical_name') or name}",
                "identity": identity,
                "matching_fields": option.get("matching_fields") or [],
                "conflicts": option.get("conflicts") or [],
                "match_confidence": option.get("score") or "0",
                "knowledge_entity_id": option.get("knowledge_entity_id"),
                "source_type": "knowledge_parallel_scope",
            }
        )
    return candidates


def candidate_identity_key(identity: dict[str, Any]) -> tuple:
    def hashable(value: Any) -> Any:
        if isinstance(value, list):
            return tuple(hashable(item) for item in value)
        if isinstance(value, dict):
            return tuple(sorted((key, hashable(item)) for key, item in value.items()))
        if isinstance(value, str):
            return _identity_token(value)
        return value

    return tuple(
        hashable(identity.get(field))
        for field in (
            "player_name",
            "season_start",
            "season_end",
            "brand_name",
            "product_name",
            "insert_name",
            "parallel_name",
            "card_number",
            "serial_total",
            "grade_company",
            "grade_value",
            "is_rookie",
            "rookie_evidence",
            "is_autograph",
            "autograph_type",
            "has_inscription",
            "inscription_text",
            "is_memorabilia",
            "memorabilia_types",
            "memorabilia_usage",
            "memorabilia_player",
            "is_patch",
        )
    )


def candidate_has_minimum_anchors(identity: dict[str, Any]) -> bool:
    """Keep concrete Top-3 hypotheses while rejecting vague player-only guesses."""
    if not identity.get("player_name"):
        return False
    anchors = sum(
        identity.get(field) not in (None, "", [], {})
        for field in (
            "season_start",
            "season_end",
            "product_name",
            "insert_name",
            "parallel_name",
            "card_number",
            "serial_total",
        )
    )
    return anchors >= 2


def identity_is_same_card(primary: dict[str, Any], candidate: dict[str, Any]) -> bool:
    compared = 0
    for field in IDENTITY_DISCRIMINATOR_FIELDS:
        value = candidate.get(field)
        if value in (None, "", [], {}) or field not in primary:
            continue
        primary_value = primary[field]
        if primary_value in (None, "", [], {}):
            continue
        compared += 1
        normalized_primary = (
            _identity_token(primary_value)
            if isinstance(primary_value, str)
            else primary_value
        )
        normalized_candidate = (
            _identity_token(value) if isinstance(value, str) else value
        )
        if normalized_primary != normalized_candidate:
            return False
    return compared >= 2


def exact_identity_confidence(result: CardRecognitionResult) -> Decimal:
    """Calibrate visual confidence down when exact-card anchors are incomplete."""
    confidence = result.confidence
    for value in (
        result.player_name,
        result.season_start,
        result.product_name,
        result.card_number,
    ):
        if value in (None, ""):
            confidence -= Decimal("0.08")
    if result.serial_total is not None and result.parallel_name is None:
        confidence -= Decimal("0.07")
    return max(Decimal("0"), min(Decimal("1"), confidence))
