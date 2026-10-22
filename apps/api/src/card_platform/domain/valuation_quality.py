from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from math import exp, log
from statistics import median

from card_platform.domain.card_identity import (
    has_misplaced_season_numbering,
    normalize_text,
)
from card_platform.domain.comparables import ComparableTier
from card_platform.domain.grade_basis import UNGRADED_SAME_CARD_BASIS
from card_platform.domain.identity_review import is_scoped_standard_refractor


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
    "public_completed_sale": EvidenceGrade.E2,
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
    if has_misplaced_season_numbering(identity):
        reasons.append("season_text_misplaced_as_card_or_serial_number")
    # ``season_start`` is the searchable release-year anchor.  Some calendar-
    # year products and older checklists do not expose an unambiguous NBA
    # season end; blocking valuation on that optional field created a needless
    # human-confirmation queue even though every marketplace query can be built.
    for field in ("player_name", "season_start", "product_name"):
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
    has_insert_anchor = bool(
        normalize_text(identity.get("insert_name") or identity.get("insert"))
    )
    has_card_number_anchor = bool(normalize_text(identity.get("card_number")))
    official_chrome_refractor = is_scoped_standard_refractor(identity)
    if parallel in {"prizm", "refractor", "parallel", "折射"} and not official_chrome_refractor:
        reasons.append("generic_parallel_name")
    # A print run plus a real card/insert anchor is already specific enough to
    # search safely. Many products number their base or insert directly and do
    # not assign a separate parallel name; requiring one invents catalog data.
    if serial_total and not parallel and not (
        has_insert_anchor or has_card_number_anchor
    ):
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
    pricing_basis: str | None = None
    seller_key: str | None = None

    @property
    def card_value(self) -> Decimal:
        """Card-only proceeds; shipping is evidence, never part of inventory value."""
        return self.price


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
    """Evidence-first estimator with explicit low-confidence proxy output."""

    def estimate(
        self, sales: list[ShadowSale], identity: dict, *, require_same_card: bool = False,
    ) -> ShadowEstimate:
        identity_blockers = identity_review_reasons(identity)
        half_life = 180 if int(identity.get("serial_total") or 999) <= 99 else 90
        eligible = []
        rejected: list[dict] = []
        for sale in sales:
            grade = evidence_grade(sale.evidence_level)
            reasons = []
            if require_same_card and sale.tier not in {ComparableTier.T0, ComparableTier.T1}:
                reasons.append("formal_price_requires_same_card_and_grade")
            if sale.tier == ComparableTier.REJECTED:
                reasons.append("identity_conflict")
            if sale.tier == ComparableTier.T3:
                reasons.append("relative_card_too_weak_for_price_center")
            if sale.tier == ComparableTier.T2 and sale.provider_code != "cardhobby":
                reasons.append("relative_card_requires_reproducible_price_adjustment")
            if sale.pricing_basis == UNGRADED_SAME_CARD_BASIS and not (
                sale.provider_code == "cardhobby"
                and sale.tier == ComparableTier.T2
                and bool(identity.get("grade_company"))
                and identity.get("grade_value") is not None
            ):
                reasons.append("invalid_ungraded_same_card_pricing_basis")
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
                tuple(rejected),
                tuple(dict.fromkeys([*identity_blockers, "no_eligible_relative_candidates"])),
            )

        # T1 may stand in for an exact sale when the identity remains strongly
        # anchored. A real CardHobby T2 transaction may still provide a useful
        # same-player/product proxy: publish it with a deliberately broad range
        # and low confidence instead of hiding the card. T3 remains context only.
        tier_priority = {
            ComparableTier.T0: 0,
            ComparableTier.T1: 1,
            ComparableTier.T2: 2,
            ComparableTier.T3: 3,
        }
        best_priority = min(tier_priority[sale.tier] for sale in eligible)
        relative_candidates = [
            sale for sale in eligible if tier_priority[sale.tier] == best_priority
        ]
        if best_priority == tier_priority[ComparableTier.T2]:
            same_card_basis = [
                sale for sale in relative_candidates
                if sale.pricing_basis == UNGRADED_SAME_CARD_BASIS
            ]
            # A verified same printed card is stronger than ordinary product
            # analogues. Keep the explicit grading basis and its uncertainty
            # attached to every selected price, even when both use tier T2.
            if same_card_basis:
                relative_candidates = same_card_basis
        for sale in eligible:
            if sale not in relative_candidates:
                rejected.append({"id": sale.id, "reasons": ["weaker_relative_tier"]})
        eligible = relative_candidates

        prices = [sale.card_value for sale in eligible]
        if len(prices) >= 5:
            center = Decimal(str(median(prices)))
            deviations = [abs(price - center) for price in prices]
            mad = Decimal(str(median(deviations)))
            if mad > 0:
                kept = []
                for sale in eligible:
                    robust_z = Decimal("0.6745") * abs(sale.card_value - center) / mad
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
            weighted.append((sale.card_value, weight))
        fair = _weighted_quantile(weighted, Decimal("0.5"))
        count = len(eligible)
        selected_tier = eligible[0].tier
        if selected_tier == ComparableTier.T2 and count == 1:
            low = fair * Decimal("0.45")
            high = fair * Decimal("1.65")
            confidence = Decimal("0.18")
        elif selected_tier == ComparableTier.T2 and count == 2:
            low = min(value for value, _ in weighted) * Decimal("0.55")
            high = max(value for value, _ in weighted) * Decimal("1.45")
            confidence = Decimal("0.22")
        elif selected_tier == ComparableTier.T2:
            low = min(
                _weighted_quantile(weighted, Decimal("0.25")) * Decimal("0.70"),
                fair * Decimal("0.60"),
            )
            high = max(
                _weighted_quantile(weighted, Decimal("0.75")) * Decimal("1.30"),
                fair * Decimal("1.40"),
            )
            confidence = min(
                Decimal("0.30"),
                Decimal("0.20") + Decimal(count) * Decimal("0.02"),
            )
        elif count == 1:
            low, high, confidence = fair * Decimal("0.65"), fair * Decimal("1.35"), Decimal("0.45")
        elif count == 2:
            low = min(value for value, _ in weighted) * Decimal("0.85")
            high = max(value for value, _ in weighted) * Decimal("1.15")
            confidence = Decimal("0.65")
        else:
            low = min(_weighted_quantile(weighted, Decimal("0.25")), fair * Decimal("0.85"))
            high = max(_weighted_quantile(weighted, Decimal("0.75")), fair * Decimal("1.15"))
            confidence = min(Decimal("0.85"), Decimal("0.62") + Decimal(count) * Decimal("0.035"))

        review: list[str] = list(identity_blockers)
        if all(sale.pricing_basis == UNGRADED_SAME_CARD_BASIS for sale in eligible):
            low = min(low, fair * Decimal("0.45"))
            high = max(high, fair * Decimal("1.65"))
            confidence = min(confidence, Decimal("0.25"))
            review.extend([
                UNGRADED_SAME_CARD_BASIS,
                "grade_premium_not_established",
                "condition_adjustment_unverified",
            ])
        if eligible[0].tier != ComparableTier.T0:
            review.append(f"relative_candidate_tier_{eligible[0].tier.value}")
        if selected_tier == ComparableTier.T2:
            review.append("cardhobby_same_type_proxy_range")
        if count < 2 and selected_tier != ComparableTier.T2:
            review.append("fewer_than_two_exact_sales")
        elif count < 2:
            review.append("single_cardhobby_proxy_sale")
        if max(sale.age_days for sale in eligible) > 180:
            review.append("stale_exact_evidence")
        providers = {sale.provider_code for sale in eligible}
        if len(providers) == 1 and count > 1:
            review.append("single_provider_concentration")
        if fair and (high - low) / fair > 1:
            review.append("interval_wider_than_center")
        if int(identity.get("serial_total") or 999) <= 10:
            review.append("ultra_rare_numbered_card")
        tier_confidence_cap = {
            ComparableTier.T0: Decimal("0.90"),
            ComparableTier.T1: Decimal("0.60"),
            ComparableTier.T2: Decimal("0.35"),
            ComparableTier.T3: Decimal("0.20"),
        }[eligible[0].tier]
        confidence = min(confidence, tier_confidence_cap)
        from card_platform.domain.sale_independence import independence_confidence_cap
        confidence = min(confidence, independence_confidence_cap(eligible))
        if identity_blockers:
            confidence = min(confidence, Decimal("0.25"))
        return ShadowEstimate(
            "estimated",
            _q(low), _q(fair), _q(high), confidence,
            tuple(sale.id for sale in eligible), tuple(rejected),
            tuple(dict.fromkeys(review)),
        )
