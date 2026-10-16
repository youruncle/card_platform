"""Bounded visual inference for private purchase costs, separate from valuations."""

import hashlib
import json
import re
from decimal import Decimal

from card_platform.domain.sketch_identity import is_sketch_card
from card_platform.domain.independent_finish import independent_finish_is_current

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
TITLE_CORRECTION_CONFLICTS = {
    '年份冲突', '赛季结束年份冲突', '标题包含其他球员，需核对多人卡或打包商品',
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


def title_correction_eligible(card, match):
    """Discovery across seller text errors; never across a physical edition claim."""
    return (
        not match['automatic']
        and bool(match['conflicts'] or '球员' in match['missing'])
        and not any(card.get(key) for key in (
            'serial_number', 'serial_total', 'print_run', 'is_numbered', 'parallel_name',
            'grade_company', 'grade_value', 'grade_cert_number', 'is_redemption',
            'is_autograph', 'is_memorabilia', 'is_patch',
        ))
        and not is_sketch_card(card.get('insert_name'))
        and set(match['conflicts']) <= TITLE_CORRECTION_CONFLICTS
        and set(match['missing']) <= SOFT_MISSING | {'球员'}
        and '产品一致' in match['reasons']
    )


def _current_title_correction(card, decision):
    from card_platform.comparable_image_verifier import visual_asset_binding, VisualCandidate
    binding = decision.get('evidence_binding') or {}
    expected_identity = visual_asset_binding(card, VisualCandidate('', '', ''), b'', None, b'')['identity_sha256']
    hashes = card.get('image_content_hashes') or {}
    if (decision.get('source') != 'cost_visual_inference'
            or decision.get('design_match') != 'same' or decision.get('finish_match') != 'same'
            or decision.get('conflict_evidence')
            or not hashes.get('front') or binding.get('target_front_sha256') != hashes['front']
            or binding.get('target_back_sha256') != hashes.get('back')
            or binding.get('identity_sha256') != expected_identity
            or not all(re.fullmatch('[a-f0-9]{64}', str(binding.get(key, ''))) for key in (
                'candidate_sha256', 'candidate_title_sha256', 'identity_sha256',
            ))
            or not independent_finish_is_current(decision.get('independent_confirmation'), binding)):
        return False
    evidence = decision.get('match_evidence') or []
    if ({row.get('kind') for row in evidence} != {'layout', 'finish'}
            or not all(row.get('source') == 'images' and row.get('both_visible') is True
                       and row.get('target_view') == row.get('candidate_view') == 'front'
                       and row.get('target') and row.get('candidate') for row in evidence)):
        return False
    for reason in decision.get('reasons', []):
        geometry = re.fullmatch(r'geometry_same_template:matches=(\d+),inliers=(\d+),ratio=([\d.]+)', str(reason))
        if geometry and int(geometry[1]) >= 60 and int(geometry[2]) >= 40 and Decimal(geometry[3]) >= Decimal('.40'):
            return True
    return False


def apply_inference(card, match, decision):
    if not decision:
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
    if (title_correction_eligible(card, match) and verdict == 'same'
            and confidence >= Decimal('.90') and _current_title_correction(card, decision)):
        return {
            **result, 'automatic': True, 'recalled': True,
            'conflicts': [], 'missing': [], 'match_method': 'image_title_correction',
            'confidence': min(confidence, Decimal('.95')),
            'seller_title_conflicts': match['conflicts'], 'seller_title_missing': match['missing'],
            'inference_warnings': ['卖家标题与实卡图片信息不一致，按当前图片核验匹配；请查看订单快照'],
            'reasons': [*match['reasons'], '独立图片观察与几何对应确认同款，纠正卖家标题错误'],
        }
    if not inference_eligible(card, match):
        return match
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
