from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from math import exp, log
from statistics import median

from card_platform.domain.card_identity import normalize_text
from card_platform.domain.comparables import ComparableTier


class EvidenceGrade(StrEnum):
    E0 = "E0"
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"


EVIDENCE_LEVEL_GRADES = {
    "confirmed_transaction": EvidenceGrade.E0,
    "ended_auction": EvidenceGrade.E1,
    "legacy_cardhobby_sale": EvidenceGrade.E1,
    "human_verified_exact_identity": EvidenceGrade.E2,
    "codex_assisted_exact_identity": EvidenceGrade.E2,
    "aggregated_sale": EvidenceGrade.E2,
    "legacy_market_comp": EvidenceGrade.E2,
    "asking_price": EvidenceGrade.E3,
    "active_listing": EvidenceGrade.E3,
    "price_guide": EvidenceGrade.E3,
    "proxy": EvidenceGrade.E4,
}


def evidence_grade(level: str | None) -> EvidenceGrade:
    return EVIDENCE_LEVEL_GRADES.get(
        normalize_text(level).replace(" ", "_"), EvidenceGrade.E3
    )


def identity_review_reasons(identity: dict) -> list[str]:
    reasons = []
    for field in ("player_name", "season_start", "season_end", "product_name"):
        if identity.get(field) in (None, ""):
            reasons.append(f"missing_{field}")
    if not any(
        identity.get(field) not in (None, "")
        for field in ("card_number", "insert_name", "set_name")
    ):
        reasons.append("missing_card_anchor")
    serial_number = identity.get("serial_number")
    serial_total = identity.get("serial_total") or identity.get("print_run")
    if serial_number is not None and serial_total is None:
        reasons.append("serial_total_unknown")
    if serial_number is not None and serial_total is not None:
        if int(serial_number) < 1 or int(serial_number) > int(serial_total):
            reasons.append("serial_number_out_of_range")
    parallel = normalize_text(identity.get("parallel_name") or identity.get("parallel"))
    product = normalize_text(identity.get("product_name"))
    if parallel in {"prizm", "refractor", "parallel", "折射"}:
        reasons.append("generic_parallel_name")
    if serial_total and not parallel:
        reasons.append("numbered_parallel_unknown")
    if parallel and product and parallel == product:
        reasons.append("parallel_matches_product")
    reasons.extend(f"identity_conflict:{item}" for item in identity.get("conflicts") or [])
    return list(dict.fromkeys(reasons))


@dataclass(frozen=True, slots=True)
class ShadowSale:
    id: str
    provider_code: str
    price: Decimal
    shipping: Decimal
    age_days: int
    tier: ComparableTier
    similarity: Decimal
    source_reliability: Decimal
    evidence_level: str


@dataclass(frozen=True, slots=True)
class ShadowEstimate:
    status: str
    low: Decimal | None
    fair: Decimal | None
    high: Decimal | None
    confidence: Decimal
    used_ids: tuple[str, ...]
    rejected: tuple[dict, ...]
    review_reasons: tuple[str, ...]


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _weighted_quantile(
    values: list[tuple[Decimal, Decimal]], quantile: Decimal
) -> Decimal:
    ordered = sorted(values, key=lambda item: item[0])
    threshold = sum((weight for _, weight in ordered), Decimal("0")) * quantile
    cumulative = Decimal("0")
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


class ShadowValuationEstimator:
    """Conservative estimator used beside, never in place of, published values."""

    def estimate(self, sales: list[ShadowSale], identity: dict) -> ShadowEstimate:
        identity_blockers = identity_review_reasons(identity)
        if identity_blockers:
            return ShadowEstimate(
                "manual_review", None, None, None, Decimal("0"), (), (),
                tuple(identity_blockers),
            )
        half_life = 180 if int(identity.get("serial_total") or 999) <= 99 else 90
        eligible = []
        rejected: list[dict] = []
        for sale in sales:
            grade = evidence_grade(sale.evidence_level)
            reasons = []
            if sale.tier != ComparableTier.T0:
                reasons.append("not_exact_identity")
            if grade not in {EvidenceGrade.E0, EvidenceGrade.E1, EvidenceGrade.E2}:
                reasons.append(f"evidence_grade_{grade.value}_not_center_eligible")
            if sale.price <= 0:
                reasons.append("non_positive_price")
            if reasons:
                rejected.append({"id": sale.id, "reasons": reasons})
            else:
                eligible.append(sale)
        if not eligible:
            return ShadowEstimate(
                "insufficient", None, None, None, Decimal("0"), (),
                tuple(rejected), ("no_exact_center_eligible_sales",),
            )

        prices = [sale.price for sale in eligible]
        if len(prices) >= 5:
            center = Decimal(str(median(prices)))
            deviations = [abs(price - center) for price in prices]
            mad = Decimal(str(median(deviations)))
            if mad > 0:
                kept = []
                for sale in eligible:
                    robust_z = Decimal("0.6745") * abs(sale.price - center) / mad
                    if robust_z > Decimal("3.5"):
                        rejected.append({"id": sale.id, "reasons": ["price_outlier_mad"]})
                    else:
                        kept.append(sale)
                eligible = kept or eligible

        weighted = []
        for sale in eligible:
            recency = Decimal(str(exp(-log(2) * sale.age_days / half_life)))
            grade_weight = {
                EvidenceGrade.E0: Decimal("1"),
                EvidenceGrade.E1: Decimal("0.78"),
                EvidenceGrade.E2: Decimal("0.62"),
            }[evidence_grade(sale.evidence_level)]
            weight = recency * grade_weight * sale.similarity * sale.source_reliability
            weighted.append((sale.price, weight))
        fair = _weighted_quantile(weighted, Decimal("0.5"))
        count = len(eligible)
        if count == 1:
            low, high, confidence = fair * Decimal("0.65"), fair * Decimal("1.35"), Decimal("0.45")
        elif count == 2:
            low = min(value for value, _ in weighted) * Decimal("0.85")
            high = max(value for value, _ in weighted) * Decimal("1.15")
            confidence = Decimal("0.65")
        else:
            low = min(_weighted_quantile(weighted, Decimal("0.25")), fair * Decimal("0.85"))
            high = max(_weighted_quantile(weighted, Decimal("0.75")), fair * Decimal("1.15"))
            confidence = min(Decimal("0.85"), Decimal("0.62") + Decimal(count) * Decimal("0.035"))

        review: list[str] = []
        if count < 2:
            review.append("fewer_than_two_exact_sales")
        if max(sale.age_days for sale in eligible) > 180:
            review.append("stale_exact_evidence")
        if any(sale.price < sale.shipping for sale in eligible):
            review.append("hammer_price_below_shipping")
        providers = {sale.provider_code for sale in eligible}
        if len(providers) == 1 and count > 1:
            review.append("single_provider_concentration")
        if fair and (high - low) / fair > 1:
            review.append("interval_wider_than_center")
        if int(identity.get("serial_total") or 999) <= 10:
            review.append("ultra_rare_numbered_card")
        return ShadowEstimate(
            "manual_review" if review else "estimated",
            _q(low), _q(fair), _q(high), confidence,
            tuple(sale.id for sale in eligible), tuple(rejected),
            tuple(dict.fromkeys(review)),
        )
