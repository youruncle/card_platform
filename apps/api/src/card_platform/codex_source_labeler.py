"""Auditable source-assisted labels for the Codex 1,000-card learning campaign.

The blind prediction is created before this module sees listing metadata.  This
module then extracts only explicit facts from the CardHobby title and merges them
with image-derived identity fields.  It deliberately leaves ambiguous fields
unknown instead of turning missing title text into a negative label.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from card_platform.recognition_benchmark import identity_matches_truth


COLOR_PARALLELS = {
    "silver": ("银折", "银平行", "silver"),
    "gold": ("金折", "金平行", "gold"),
    "red": ("红折", "红平行", "red"),
    "blue": ("蓝折", "蓝平行", "blue"),
    "green": ("绿折", "绿平行", "green"),
    "purple": ("紫折", "紫平行", "purple"),
    "orange": ("橙折", "橙平行", "orange"),
    "pink": ("粉折", "粉平行", "pink"),
    "black": ("黑折", "黑平行", "black"),
}
NON_BASKETBALL_PATTERN = re.compile(
    r"羽毛球|足球|棒球|橄榄球|冰球|网球|赛车|\bF1\b|\bMLB\b|\bNFL\b|\bNHL\b",
    re.IGNORECASE,
)
PRODUCT_ALIASES = (
    ("National Treasures", ("national treasures", "国宝")),
    ("Immaculate Collection", ("immaculate", "爱咪咪")),
    ("Court Kings", ("court kings", "油画")),
    ("Donruss Optic", ("donruss optic", "杜蕾斯op")),
    ("Topps Chrome", ("topps chrome",)),
    ("Topps Inception", ("topps inception", "inception", "盗梦空间")),
    ("Topps Now", ("topps now",)),
    ("Prizm", ("panini prizm", "帕尼尼prizm", " pz ")),
    ("Mosaic", ("panini mosaic", "马赛克")),
    ("Noir", ("panini noir", " noir ", "诺尔")),
    ("Obsidian", ("panini obsidian", "黑曜石")),
    ("Impeccable", ("panini impeccable",)),
    ("Rookies & Stars", ("rookies and stars",)),
    ("Threads", ("panini threads",)),
    ("Match Attax", ("match attax",)),
    ("Bowman", ("topps bowman",)),
)
FALSE_INSERT_NAMES = {
    "rookie",
    "rookierc",
    "rookierccard",
    "rccard",
}
GENERIC_PARALLELS = {
    "prizm",
    "prizmblack",
    "noir",
    "obsidian",
    "refractor",
    "parallel",
}


def _token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _explicit_season(title: str) -> tuple[int, int] | None:
    match = re.search(r"\b(19\d{2}|20\d{2})\s*[-–]\s*(\d{2,4})\b", title)
    if not match:
        return None
    start = int(match.group(1))
    raw_end = match.group(2)
    end = int(raw_end) if len(raw_end) == 4 else (start // 100) * 100 + int(raw_end)
    if end < start:
        end += 100
    return start, end


def _explicit_serial(title: str) -> tuple[int | None, int | None]:
    fraction = re.search(r"(?<!\d)(\d{1,4})\s*/\s*(\d{1,4})(?!\d)", title)
    if fraction:
        number, total = int(fraction.group(1)), int(fraction.group(2))
        if 0 < number <= total:
            return number, total
    total = re.search(r"(?<!\d)(\d{1,4})\s*[编遍](?!\d)", title)
    return (None, int(total.group(1))) if total else (None, None)


def _explicit_title_colors(title: str, player_name: object) -> list[str]:
    normalized = title.casefold()
    player_tokens = set(re.findall(r"[a-z]+", str(player_name or "").casefold()))
    colors: list[str] = []
    for canonical, aliases in COLOR_PARALLELS.items():
        chinese_aliases = [alias for alias in aliases if re.search(r"[\u4e00-\u9fff]", alias)]
        chinese_color = chinese_aliases[0][0] if chinese_aliases else ""
        if any(alias.casefold() in normalized for alias in chinese_aliases) or (
            chinese_color
            and re.search(
                rf"{re.escape(chinese_color)}[\u4e00-\u9fff]{{0,4}}折",
                title,
            )
        ):
            colors.append(canonical)
            continue
        if canonical in player_tokens:
            # Black/Green are common player surnames.  Require an actual parallel
            # descriptor when the English color also appears in the player name.
            pattern = rf"\b{re.escape(canonical)}\b\s+(?:prizm|parallel|refractor|wave|shimmer|pulsar)"
        else:
            pattern = rf"\b{re.escape(canonical)}\b"
        if re.search(pattern, normalized):
            colors.append(canonical)
    return colors


def _canonical_parallel_color(value: object) -> str | None:
    text = str(value or "")
    if not re.search(
        r"refractor|prizm|holo|chrome|wave|shimmer|cracked\s*ice|laser",
        text,
        re.IGNORECASE,
    ):
        return None
    colors = [
        canonical.title()
        for canonical in COLOR_PARALLELS
        if re.search(rf"\b{re.escape(canonical)}\b", text, re.IGNORECASE)
    ]
    colors = list(dict.fromkeys(colors))
    return colors[0] if len(colors) == 1 else None


def build_source_assisted_truth(
    prediction: dict[str, Any] | None,
    source_title: str,
) -> tuple[dict[str, Any] | None, list[str], float]:
    candidates = (prediction or {}).get("candidates") or []
    if not candidates:
        return None, ["图片盲预测无可用候选"], 0.0
    truth = deepcopy(candidates[0].get("identity") or {})
    if not truth.get("player_name"):
        return None, ["图片与标题均不足以确认球员"], 0.0

    evidence = ["图片盲预测先于来源揭示完成", f"卡淘标题：{source_title[:240]}"]
    normalized = f" {source_title.casefold()} "

    season = _explicit_season(source_title)
    if season:
        truth["season_start"], truth["season_end"] = season
        evidence.append(f"标题明确赛季 {season[0]}-{season[1]}")

    for canonical, aliases in PRODUCT_ALIASES:
        if any(alias in normalized for alias in aliases):
            truth["product_name"] = canonical
            evidence.append(f"标题明确产品 {canonical}")
            break

    serial_number, serial_total = _explicit_serial(source_title)
    if serial_total:
        truth["serial_number"] = serial_number
        truth["serial_total"] = serial_total
        truth["is_numbered"] = True
        evidence.append(
            f"标题明确限编 {serial_number}/{serial_total}"
            if serial_number
            else f"标题明确 /{serial_total} 编"
        )

    grade = re.search(
        r"\b(PSA|BGS|CGC|SGC|RFE|BEM|CBA|GMA)\s*(10|9(?:\.5)?|8(?:\.5)?)\b",
        source_title,
        re.IGNORECASE,
    )
    if grade:
        truth["grade_company"] = grade.group(1).upper()
        truth["grade_value"] = grade.group(2)
        evidence.append(f"标题明确评级 {grade.group(1).upper()} {grade.group(2)}")

    if re.search(r"新秀|\bRC\b|\brookie\b", source_title, re.IGNORECASE):
        truth["is_rookie"] = True
        # Evidence wording is explanatory provenance, not a card-identity key.
        # Keep it outside the structured truth so equivalent RC evidence phrases
        # do not turn an otherwise exact candidate into a false negative.
        truth["rookie_evidence"] = []
        evidence.append("标题明确 RC/新秀")

    title_is_auto = bool(
        re.search(r"签字|签名|卡签|贴签|\bauto(?:graph)?\b|\bRPA\b", source_title, re.I)
    )
    if title_is_auto:
        truth["is_autograph"] = True
        if "卡签" in source_title:
            truth["autograph_type"] = "on_card"
        elif "贴签" in source_title:
            truth["autograph_type"] = "sticker"
        evidence.append("标题明确签字及签字载体（若有）")

    memorabilia_tokens = re.search(
        r"球衣|球鞋|物料|切割|\bpatch\b|\bjersey\b|\brelic\b|\bRPA\b",
        source_title,
        re.I,
    )
    if memorabilia_tokens:
        truth["is_memorabilia"] = True
        types: list[str] = []
        if re.search(r"球鞋|sneaker", source_title, re.I):
            types.append("sneaker")
        if re.search(r"球衣|jersey", source_title, re.I):
            types.append("jersey")
        if re.search(r"\bpatch\b|盾|队标|logo", source_title, re.I):
            types.append("patch")
            truth["is_patch"] = True
        if types:
            truth["memorabilia_types"] = sorted(set(types))
        if re.search(r"亲穿|player[- ]?worn", source_title, re.I):
            truth["memorabilia_usage"] = "player_worn"
        elif re.search(r"比赛使用|game[- ]?used", source_title, re.I):
            truth["memorabilia_usage"] = "game_used"
        evidence.append("标题明确物料类型/使用属性（若有）")

    title_colors = _explicit_title_colors(source_title, truth.get("player_name"))
    if len(title_colors) == 1:
        truth["parallel_name"] = title_colors[0].title()
        evidence.append(f"标题明确 {title_colors[0]} 颜色平行")
    else:
        normalized_parallel = _canonical_parallel_color(truth.get("parallel_name"))
        if normalized_parallel:
            truth["parallel_name"] = normalized_parallel
            evidence.append("图片复合平行名按统一颜色本体归一")

    if _token(truth.get("insert_name")) in FALSE_INSERT_NAMES:
        truth["insert_name"] = None
    if _token(truth.get("parallel_name")) in GENERIC_PARALLELS:
        truth["parallel_name"] = None

    anchors = sum(
        truth.get(field) not in (None, "", [], {})
        for field in (
            "season_start",
            "product_name",
            "card_number",
            "insert_name",
            "parallel_name",
            "serial_total",
            "grade_company",
        )
    )
    confidence = min(0.99, 0.74 + 0.035 * anchors + 0.01 * max(0, len(evidence) - 2))
    return truth, evidence, round(confidence, 4)


def label_against_truth(
    prediction: dict[str, Any] | None,
    truth: dict[str, Any] | None,
) -> str:
    if not truth:
        return "insufficient_image"
    for rank, candidate in enumerate((prediction or {}).get("candidates") or [], start=1):
        if identity_matches_truth(candidate.get("identity"), truth):
            return f"top{rank}_correct"
    return "none_correct"
