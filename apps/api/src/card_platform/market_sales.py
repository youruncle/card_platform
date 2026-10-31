"""Deterministic CardHobby observations; model judgments never supply prices."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from statistics import median
import re
from zoneinfo import ZoneInfo


IDENTITY_FIELDS = (
    "brand_name",
    "set_name",
    "is_numbered",
    "is_patch",
    "autograph_type",
    "memorabilia_type",
    "player_name",
    "product_name",
    "season",
    "card_number",
    "parallel_name",
    "serial_total",
    "grade_company",
    "grade_value",
    "is_autograph",
    "is_memorabilia",
    "condition",
)
REQUIRED_FIELDS = (
    "brand_name",
    "set_name",
    "player_name",
    "product_name",
    "season",
    "card_number",
    "parallel_name",
    "grade_company",
    "condition",
)


def money(value):
    try:
        result = Decimal(str(value))
        return str(result) if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def normalize_row(row):
    reasons = []
    try:
        ended = datetime.strptime(str(row.get("EffectiveDate")), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=ZoneInfo("Asia/Shanghai")
        )
        if ended > datetime.now(timezone.utc):
            raise ValueError("future")
        date = ended.isoformat()
    except ValueError:
        date = None
        reasons.append("invalid_or_future_end_time")
    try:
        bids = int(
            row.get("OutCount") if row.get("OutCount") is not None else row.get("PriceCount") or 0
        )
        if int(row.get("Status") or 0) != -2 or int(row.get("ByWay") or 0) != 2 or bids <= 0:
            reasons.append("not_public_ended_auction_with_bids")
    except (ValueError, TypeError, OverflowError):
        bids = 0
        reasons.append("invalid_sale_status")
    price = money(row.get("LowestPrice"))
    if price is None or Decimal(price) <= 0:
        reasons.append("unknown_closing_price")
    item_id = str(row.get("ID") or "")
    if not item_id.isdigit() or not row.get("Title"):
        reasons.append("missing_item_identity")
    shipping = money(row.get("NewPostageMoney"))
    return dict(
        candidate_id=item_id,
        title=str(row.get("Title") or ""),
        url=f"https://cardhobby.com.cn/market/item/{item_id}",
        price=price,
        shipping=shipping,
        shipping_status="known" if shipping is not None else "unknown",
        currency="CNY",
        ended_at=date,
        bid_count=bids,
        seller_id=str(row.get("SellMemberID") or ""),
        sale_eligible=not reasons,
        sale_reasons=reasons,
        payment_confirmed=False,
        price_basis="public_auction_hammer",
        thumbnail_url=str(row.get("TitImg") or ""),
        images={},
        detail_verified=False,
    )


def _norm(value):
    return value.strip().casefold() if isinstance(value, str) else value


def build_report(target, candidates, reviews, has_target_images):
    latest = {r["candidate_id"]: r for r in reviews}
    missing = [k for k in REQUIRED_FIELDS if not target.get(k)]
    if target.get("grade_company") != "raw" and not target.get("grade_value"):
        missing.append("grade_value")
    if any(
        target.get(k) is None for k in ("is_autograph", "is_memorabilia", "is_numbered", "is_patch")
    ):
        missing.append("autograph_or_memorabilia_unknown")
    if target.get("is_numbered") and target.get("serial_total") is None:
        missing.append("serial_total")
    for flag, field in (("is_autograph", "autograph_type"), ("is_memorabilia", "memorabilia_type")):
        if target.get(flag) and not target.get(field):
            missing.append(field)
    accepted, excluded, used_images, used_serials, sellers = [], [], set(), set(), set()
    reviewed = 0
    for c in candidates:
        r = latest.get(c["candidate_id"])
        reason = None
        if not r or r.get("evidence_digest") != c.get("evidence_digest"):
            reason = "unreviewed_or_stale"
        else:
            reviewed += 1
            if r.get("classification") != "exact":
                reason = r.get("classification", "uncertain")
            elif not c["sale_eligible"] or not c.get("detail_verified"):
                reason = "sale_evidence_unverified"
            elif (
                not c.get("images")
                or r.get("lot") is not False
                or not r.get("condition_comparable")
                or r.get("conflicts")
            ):
                reason = "image_lot_condition_or_identity_conflict"
            elif any(
                _norm(target.get(k)) != _norm(r.get("identity", {}).get(k)) for k in IDENTITY_FIELDS
            ):
                reason = "identity_mismatch"
            elif r.get("duplicate_of"):
                reason = "duplicate_physical_card"
            elif not c.get("seller_id"):
                reason = "unknown_seller"
            elif datetime.fromisoformat(c["ended_at"]) < datetime.now(timezone.utc) - timedelta(
                days=180
            ):
                reason = "older_than_180_days"
        if reason:
            excluded.append(dict(candidate_id=c["candidate_id"], reason=reason))
            continue
        hashes = {v["sha256"] for v in c["images"].values()}
        serial = _norm(r.get("serial_number"))
        if target.get("is_numbered"):
            match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", serial or "")
            if (
                not match
                or int(match[2]) != target.get("serial_total")
                or not 1 <= int(match[1]) <= int(match[2])
            ):
                excluded.append(
                    dict(candidate_id=c["candidate_id"], reason="unverified_physical_serial")
                )
                continue
            serial = f"{int(match[1])}/{int(match[2])}"
        if hashes & used_images or (serial and serial in used_serials):
            excluded.append(dict(candidate_id=c["candidate_id"], reason="duplicate_physical_card"))
            continue
        used_images.update(hashes)
        if serial:
            used_serials.add(serial)
        sellers.add(c["seller_id"])
        accepted.append(
            {
                k: c[k]
                for k in (
                    "candidate_id",
                    "url",
                    "price",
                    "shipping",
                    "currency",
                    "ended_at",
                    "seller_id",
                )
            }
        )
    complete = reviewed == len(candidates)
    estimate = None
    if has_target_images and not missing and complete and len(sellers) >= 3:
        # One median per seller prevents one large consigner dominating the center.
        by_seller = [
            [Decimal(c["price"]) for c in accepted if c["seller_id"] == s] for s in sorted(sellers)
        ]
        centers = [median(prices) for prices in by_seller]
        all_prices = [Decimal(c["price"]) for c in accepted]
        estimate = dict(
            low=str(min(all_prices)),
            median=str(median(centers)),
            high=str(max(all_prices)),
            currency="CNY",
            shipping_included=False,
        )
    return dict(
        estimate=estimate,
        accepted=accepted,
        excluded=excluded,
        reviewed_count=reviewed,
        candidate_count=len(candidates),
        review_complete=complete,
        independent_sellers=len(sellers),
        missing_target_fields=missing,
        target_images_available=bool(has_target_images),
        source="cardhobby_only",
        price_basis="public_auction_hammer",
        payment_confirmed=False,
        valuation_published=False,
        ledger_modified=False,
        note="卡淘公开落槌观察，未确认付款；区间为观察最小/最大值，中位数按卖家等权；非正式双市场估值。",
    )
