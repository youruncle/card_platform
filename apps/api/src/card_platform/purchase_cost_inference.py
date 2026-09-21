"""Bounded visual inference for private purchase costs, separate from valuations."""

import hashlib
import json
from decimal import Decimal

from card_platform.domain.sketch_identity import is_sketch_card

SOFT_MISSING = {
    "年份",
    "品牌",
    "产品",
    "Insert",
    "库存未确认 Insert",
    "库存未确认平行",
    "平行完整名称",
    "平行俗称需要当前身份及图片绑定的同款复核",
}


def inference_key(card, line, order_hash):
    line = {key: line.get(key) for key in ("item_url", "title", "image_urls", "item_id")}
    return hashlib.sha256(
        json.dumps(
            {"card": card, "line": line, "order_hash": order_hash},
            sort_keys=True,
            default=str,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def inference_eligible(card, match):
    # Never infer across a hard contradiction or uniquely drawn original art.
    return (
        not match["automatic"]
        and match["recalled"]
        and not match["conflicts"]
        and not is_sketch_card(card.get("insert_name"))
        and not card.get("grade_company")
        and "球员" not in match["missing"]
        and set(match["missing"]) <= SOFT_MISSING
        and ("产品一致" in match["reasons"] or "Insert 一致" in match["reasons"])
    )


def apply_inference(card, match, decision):
    if not inference_eligible(card, match) or not decision:
        return match
    verdict = decision.get("verdict")
    confidence = Decimal(str(decision.get("confidence", 0)))
    result = {**match, "visual_inference": decision}
    if verdict == "different":
        return {
            **result,
            "conflicts": [*match["conflicts"], "图片核心信息不一致"],
            "automatic": False,
        }
    geometry = any(
        str(r).startswith("geometry_same_template:") for r in decision.get("reasons", [])
    )
    # Similar composition alone is insufficient for a specific coloured/numbered finish.
    compatible = (
        verdict == "compatible"
        and confidence >= Decimal(".85")
        and geometry
        and not card.get("parallel_name")
        and not card.get("serial_total")
        and not card.get("print_run")
    )
    same = verdict == "same" and confidence >= Decimal(".80")
    if not (same or compatible):
        return result
    return {
        **result,
        "automatic": True,
        "match_method": "inferred",
        "confidence": min(confidence, Decimal(".89")),
        "inference_warnings": [
            *match.get("soft_differences", []),
            *match["missing"],
            "基于图片与核心身份推测匹配，请查看订单快照核对",
            "仅比较卖家商品图；未单独核验卖家背面、暗伤或同款的实物唯一性",
        ],
        "reasons": [*match["reasons"], "图片与核心信息支持推测匹配；非全部字段精确一致"],
    }
