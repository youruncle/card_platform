"""Private purchase bundle observations and cent-exact allocation, never market values."""

from decimal import Decimal
import re
import unicodedata
from card_platform.domain.player_aliases import (PLAYER_SEARCH_ALIASES, matching_player_aliases, player_roster_components, query_player_aliases)

from pydantic import BaseModel, Field, ValidationError, model_validator


CHARACTER_ALIASES = (
    ("Harley Quinn", "哈莉奎茵", "小丑女", "哈莉·奎茵"),
    ("Poison Ivy", "毒藤女"),
)


def purchase_player_aliases(player, knowledge, *, discovery=False):
    aliases = list(knowledge.aliases_for("player", player)) or [str(player)]
    if discovery:
        aliases.extend(knowledge.query_aliases_for("player", player))
    folded = "".join(c for c in unicodedata.normalize("NFKD", str(player)) if not unicodedata.combining(c))
    if folded != player:
        aliases.extend((folded, *knowledge.aliases_for("player", folded)))
    aliases.extend(re.sub(r"['’.]", "", str(value)) for value in tuple(aliases))
    for value in tuple(aliases):
        key = re.sub(r"[^\w]+", " ", value.casefold()).strip()
        aliases.extend(PLAYER_SEARCH_ALIASES.get(key, ()) if discovery else matching_player_aliases(key))
        if discovery:
            aliases.extend(query_player_aliases(value))
    for group in CHARACTER_ALIASES:
        if any(str(a).casefold() == name.casefold() for a in aliases for name in group):
            aliases.extend(group)
    if discovery:
        parts = player_roster_components(player)
        if len(parts) > 1:
            for part in parts:
                aliases.extend(purchase_player_aliases(part, knowledge, discovery=True))
    return tuple(dict.fromkeys(aliases))


def split_bundle(price, shipping, count):
    if type(count) is not int or not 2 <= count <= 20:
        raise ValueError("包内张数必须明确且在2至20之间")
    amounts = []
    for value in (price, shipping):
        value = Decimal(value)
        if not value.is_finite() or value < 0 or value * 100 != int(value * 100):
            raise ValueError("金额必须为非负整分")
        amounts.append(divmod(int(value * 100), count))
    return [
        dict(
            bundle_index=i + 1,
            unit_price=Decimal(amounts[0][0] + (i < amounts[0][1])) / 100,
            shipping=Decimal(amounts[1][0] + (i < amounts[1][1])) / 100,
        )
        for i in range(count)
    ]


class BundleObservation(BaseModel):
    card_count: int = Field(ge=2, le=20, strict=True)
    matched_index: int = Field(ge=1, le=20, strict=True)
    complete: bool
    unique_match: bool
    verdict: str = Field(pattern="^(same|different|uncertain)$")
    confidence: Decimal = Field(ge=0, le=1)
    design_match: str = Field(pattern="^(same|different|unknown)$")
    finish_match: str = Field(pattern="^(same|different|unknown)$")
    reasons: list[str] = Field(min_length=1)
    card_boxes: list[list[float]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_layout(self):
        boxes = self.card_boxes
        if not boxes:
            return self  # Human paired-image observations may use explicit ordinal positions.
        if len(boxes) != self.card_count:
            raise ValueError("包内卡框数量不一致")
        for i, box in enumerate(boxes):
            if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
                raise ValueError("无效卡框")
            for other in boxes[:i]:
                intersection = max(0, min(box[2], other[2])-max(box[0], other[0])) * max(0, min(box[3], other[3])-max(box[1], other[1]))
                area = min((box[2]-box[0])*(box[3]-box[1]), (other[2]-other[0])*(other[3]-other[1]))
                if intersection / area > .15:
                    raise ValueError("卡框重叠，无法唯一分配位置")
        pending = sorted(range(len(boxes)), key=lambda i: (boxes[i][1]+boxes[i][3])/2)
        ordered = []
        while pending:
            first = boxes[pending[0]]
            center = (first[1]+first[3])/2
            row = [i for i in pending if abs((boxes[i][1]+boxes[i][3])/2-center) < min(first[3]-first[1], boxes[i][3]-boxes[i][1]) / 2]
            ordered.extend(sorted(row, key=lambda i: boxes[i][0]))
            pending = [i for i in pending if i not in row]
        if ordered != list(range(len(boxes))):
            raise ValueError("包内位置必须按行从左到右排列")
        return self


def same_bundle_layout(stored, decision):
    """Keep a physical lot's slot identity stable across separate observations."""
    boxes, other = stored.get('card_boxes') or [], decision.get('card_boxes') or []
    if not boxes and not other:
        return True
    if len(boxes) != len(other) or not boxes:
        return False
    if stored.get('candidate_image_hash') != (decision.get('image_hashes') or {}).get('candidate'):
        return False
    for a, b in zip(boxes, other):
        intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
        union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1])-intersection
        if union <= 0 or intersection / union < .8:
            return False
    return True


def bundle_match(decision, index):
    result = dict(
        confidence=Decimal(0),
        reasons=[],
        conflicts=[],
        missing=[],
        automatic=False,
        recalled=False,
        match_method="bundle_equal_split",
        soft_differences=[],
    )
    if not decision or decision.get("contract") != "purchase-bundle-v1":
        return result
    if decision.get("source") not in {
        "purchase_bundle_vision",
        "codex_paired_image_review",
        "human_paired_image_review",
    }:
        return result
    if decision.get("source") == "purchase_bundle_vision":
        comparison = decision.get("slot_comparison") or {}
        if not (
            (decision.get("geometry") or {}).get("verified_slot")
            and len(decision.get("card_boxes") or []) == decision.get("card_count")
            and comparison.get("verdict") == "same"
            and comparison.get("design_match") == comparison.get("finish_match") == "same"
            and Decimal(str(comparison.get("confidence", 0))) >= Decimal(".90")
        ):
            return result
    try:
        observation = BundleObservation.model_validate(decision)
    except ValidationError:
        return result
    if not (
        observation.complete
        and observation.unique_match
        and observation.matched_index <= observation.card_count
        and index == observation.matched_index
        and observation.verdict == "same"
        and observation.design_match == observation.finish_match == "same"
        and observation.confidence >= Decimal(".90")
    ):
        return result
    return {
        **result,
        "confidence": observation.confidence,
        "automatic": True,
        "recalled": True,
        "reasons": ["图片确认包内对应单卡；按整包张数均摊", *observation.reasons],
        "bundle_review": decision,
        "inference_warnings": [
            "仅核验所提供商品图；未展示的卖家背面及实物流水号未核验",
            "商品价与运费分别均摊，余分按包内位置分配",
        ],
    }
