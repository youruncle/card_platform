"""Separate card identity labels from biography prose without catalog guessing."""

from __future__ import annotations

import re

from card_platform.domain.serial_evidence import serial_field_evidence, unique_visible_serial


def normalize_team_parallel(result):
    """Reject team text used as a parallel, without guessing a replacement."""
    token = re.sub(r"[^a-z]", "", str(result.parallel_name or "").casefold())
    evidence = " ".join(result.field_evidence.get("parallel_name") or [])
    # Specific labels (Gold, Golden Treasures, etc.) are not bare team text.
    if token not in {"golden", "goldenstate", "goldenstatewarriors"} or not (
        token != "golden"
        or re.search(r"golden\s+state|球队|队名|球衣[^。;；]{0,40}(?:暗示|推断)", evidence, re.I)
    ):
        return result
    updates = {"parallel_name": None, "caveats": list(dict.fromkeys([
        *result.caveats,
        "team_text_not_parallel: 球队/球衣文字不能确认平行版本，已留空待核对",
    ]))}
    if result.serial_total is None and unique_visible_serial(serial_field_evidence(result.field_evidence)) is None:
        updates["is_numbered"] = None
    return result.model_copy(update=updates)


def is_biography_insert(value: object) -> bool:
    text = str(value or "").strip()
    return bool(
        re.match(r"^(?:rewind|skills|next\s+level|career\s+(?:summary|highlights)|biography)\s*[:：]", text, re.I)
        and len(re.findall(r"[A-Za-z]+", text)) >= 7
    )


def normalize_catalog_evidence(result):
    """Discard a sentence accidentally promoted to an insert, retaining evidence.

    This does not guess the missing product, year or a replacement insert.
    Whole-card text is not an identity catalogue: prose stays in field_evidence
    and the warning explains why the search anchor was removed.
    """
    result = normalize_team_parallel(result)
    if re.fullmatch(r"(?:(?:top|upper|bottom|lower)[\s_-]*(?:left|right)|[左右][上下](?:角)?)", str(result.card_number or '').strip(), re.I):
        original = result.card_number
        result = result.model_copy(update={
            'card_number':None,
            'field_evidence':{**result.field_evidence,'card_number':list(dict.fromkeys([
                *result.field_evidence.get('card_number',[]), f'原始识别位置说明（非卡号）：{original}'
            ]))},
            'caveats':list(dict.fromkeys([*result.caveats,'卡号字段是位置说明而非印刷卡号，已清空；需按原图No./#核实，不凭位置猜卡号。'])),
        })
    if not is_biography_insert(result.insert_name):
        return result
    return result.model_copy(update={
        "insert_name": None,
        "field_evidence": {
            **result.field_evidence,
            "insert_name": list(dict.fromkeys([
                *result.field_evidence.get("insert_name", []),
                f"原始识别正文（非特卡名）：{result.insert_name}",
            ])),
        },
        "caveats": list(dict.fromkeys([
            *result.caveats,
            "背面履历正文被误置为特卡名，已清空该检索锚点；未据此猜测产品或年份。",
        ])),
    })


def complete_visible_roster(names: list[str]) -> str:
    """Keep every independently observed front subject, never the first only."""
    return ", ".join(dict.fromkeys(name.strip() for name in names if name.strip()))
