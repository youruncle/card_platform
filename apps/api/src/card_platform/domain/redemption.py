"""Physical redemption vouchers are distinct from their promised cards."""

import re
from typing import Any


def redemption_identity(values: dict[str, Any]) -> tuple[bool, str]:
    text = " ".join(
        str(values.get(key) or "")
        for key in (
            "notes",
            "purchase_context",
            "title",
            "insert_name",
            "insert",
        )
    )
    # Specific voucher phrases only: congratulations alone also appears on relic cards.
    detected = bool(
        re.search(
            r"\bredemption\b|\byou are due to receive\b|交换(?:板|卡)?|兑换[板卡券]", text, re.I
        )
    )
    is_redemption = bool(values.get("is_redemption")) or detected
    status = str(values.get("redemption_status") or "unknown")
    if status not in {"unknown", "unredeemed", "redeemed", "expired"}:
        status = "unknown"
    status_text = re.sub(
        r"\b(?:not|never)\s+redeemed\b|未兑换|未兑奖", "unredeemed", text, flags=re.I
    )
    if re.search(
        r"\b(?:already\s+)?redeemed\b|\bused\s+(?:redemption\s+)?code\b|已兑[换奖]|已使用|空板|废板",
        status_text,
        re.I,
    ):
        status = "redeemed"
        is_redemption = True
    elif re.search(r"\bexpired\b|已过期|过期交换", status_text, re.I):
        status = "expired"
        is_redemption = True
    # An intact scratch coating is a surface observation, not backend evidence
    # that the redemption right remains unused. Preserve explicit state claims.
    elif re.search(r"\bunredeemed\b|\bunused\b|未兑[换奖]", status_text, re.I):
        if is_redemption:
            status = "unredeemed"
    return is_redemption, status


def _rights_withheld(title: str) -> bool:
    withheld = (
        r"(?:只|仅)(?:发|售)板(?:\s*不发(?:兑换)?码)?|(?:发(?:板|卡)\s*)?不发(?:兑换)?码"
        r"|不(?:含|带)(?:兑换)?码|\b(?:redemption\s+)?code\s+(?:not\s+included|excluded)\b"
        r"|\b(?:no|without)\s+(?:redemption\s+)?code\b"
    )
    # A photograph can conceal a valid code. That is not a sale excluding it.
    claims = re.sub(r"\bno\s+(?:redemption\s+)?code\s+(?:visible|shown|displayed)\b",
                    "", title, flags=re.I)
    # Remove a negated withholding proposition, rather than stripping 'not'
    # from affirmative 'code not included'. Other positive clauses still apply.
    claims = re.sub(r"(?:并不是|不是|并非|並非|\bnot\s+)\s*(?:" + withheld + ")",
                    "", claims, flags=re.I)
    return re.search(withheld, claims, re.I) is not None


def redemption_conflict(target: dict[str, Any], title: str) -> str | None:
    target_redemption, target_status = redemption_identity(target)
    sale_redemption, sale_status = redemption_identity({"title": title})
    if target_redemption and _rights_withheld(title):
        return "redemption_rights_withheld"
    if target_redemption != sale_redemption:
        return "redemption_form_conflict"
    if target_redemption:
        if target_status in {"redeemed", "expired"} or sale_status in {"redeemed", "expired"}:
            return "redemption_unusable_or_expired"
    return None
