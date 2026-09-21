"""Immutable public-auction facts required before accepting a price observation."""
from datetime import UTC, datetime
from decimal import Decimal


def public_auction_rejection_reasons(
    *, occurred_at: datetime, bid_count: int | None, sale_type: str,
    evidence_level: str, fx_rate_to_base: Decimal, now: datetime | None = None,
) -> list[str]:
    reasons = []
    if not bid_count or bid_count < 1:
        reasons.append("public_auction_has_no_verified_bid")
    if sale_type != "auction":
        reasons.append("public_auction_sale_type_not_verified")
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        reasons.append("sale_timestamp_timezone_missing")
    elif occurred_at > (now or datetime.now(UTC)):
        reasons.append("sale_timestamp_in_future")
    if evidence_level != "ended_auction":
        reasons.append("public_auction_does_not_prove_payment")
    if fx_rate_to_base != Decimal("1"):
        reasons.append("cardhobby_cny_source_has_unverified_fx")
    return reasons
