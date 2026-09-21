"""Explicit pricing basis for a graded card with only same-card ungraded sales."""

from card_platform.domain.listing_title import is_graded_listing


UNGRADED_SAME_CARD_BASIS = "ungraded_same_card_no_grade_premium"
UNGRADED_SAME_CARD_NOTE = (
    "采用同款未标评级成交作为卡片基础价；缺少同公司同分数成交，"
    "未计评级溢价，评级与品相差异尚未定价，估值区间已放宽并降低置信度。"
)


def can_use_ungraded_basis(
    identity: dict, title: str, grade_company: str | None, grade_value: object
) -> bool:
    """Check grading direction only; callers must separately prove the same card."""
    return bool(
        identity.get("grade_company")
        and identity.get("grade_value") is not None
        and grade_company is None
        and grade_value is None
        and not is_graded_listing(title)
    )
