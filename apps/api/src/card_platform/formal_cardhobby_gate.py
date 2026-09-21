"""Final same-card admission, independent of discovery tiers and stale markers."""
import re
from decimal import Decimal

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.auction_evidence import public_auction_rejection_reasons
from card_platform.domain.redemption import redemption_conflict
from card_platform.domain.comparables import (
    Classification, ComparableTier, ComparableClassifier, ComparableInput,
)
from card_platform.domain.grade_basis import UNGRADED_SAME_CARD_BASIS
from card_platform.ingestion.cardhobby import CardHobbySale, assess_cardhobby_candidate
from card_platform.domain.listing_title import is_graded_listing, is_multi_card_listing
from card_platform.marketplace_aliases import requires_visual_confirmation

FORMAL_CARDHOBBY_CONTRACT = "same-card-source-revalidated-v7"


def title_requires_named_parallel(identity: dict, title: str) -> bool:
    """A similar layout cannot fill an unknown target's version-bearing color.

    Equal print runs also occur across distinct finishes (solid/wave/shimmer).
    Generic foil wording is intentionally excluded because many base inserts
    have an intrinsic finish. Require a specific color/finish claim instead.
    """
    if str(identity.get("parallel_name") or identity.get("parallel") or "").strip():
        return False
    return bool(re.search(
        r"(?:红|紅|蓝|藍|绿|綠|金|银|銀|紫|黑|白|橙|粉|黄|黃)"
        r"(?:色)?\s*(?:波纹|波紋|波浪|碎冰|冰晶|闪光|閃光|亮片|格子)?\s*(?:折|平行)"
        r"|\b(?:gold|silver|red|blue|green|purple|black|white|orange|pink|yellow|aqua|teal)"
        r"[\s-]+(?:(?:wave|shimmer|ice|disco|pulsar|scope|laser)[\s-]+)?"
        r"(?:refractor|prizm|parallel)\b"
        r"|(?:中子|宇宙|星爆|银河|銀河)\s*(?:折|平行)"
        r"|\b(?:cosmic|galactic|neutron|supernova)\s+(?:parallel|refractor)\b",
        title, re.I,
    ))


def title_discloses_damage(title: str) -> bool:
    terms = (
        r"微瑕|瑕疵|有伤|有傷|折痕|磕角|压痕|壓痕|轻印|輕印|印痕|凹痕|划痕|劃痕|白角"
        r"|印刷线|印刷線|刮伤|刮傷|磨损|磨損"
    )
    # A spot disclosure is contextual: printed dotted artwork is not damage.
    spots = re.sub(r'(?:无|無|没有|沒有|未见|未見)\s*(?:明显|明顯)?\s*(?:小)?花[点點]', '', title)
    spots = re.sub(r'花[点點]\s*(?:图案|圖案|设计|設計|纹样|紋樣)', '', spots)
    if re.search(r'(?:边框|邊框|正面|背面|表面).{0,8}(?:小)?花[点點]', spots):
        return True
    text = re.sub(r"(?:无|無|没有|沒有|未见|未見)(?:明显|明顯)?\s*(?:" + terms + ")", " ", title)
    text = re.sub(r"\b(?:no|without|not)\s+(?:visible\s+)?(?:damage[d]?|crease[sd]?)\b", " ", text, flags=re.I)
    return bool(re.search(terms + r"|\bdamaged\b|\bcrease[sd]?\b", text, re.I))


def _specific_parallel_unconfirmed(identity: dict, classification: Classification) -> bool:
    parallel = str(identity.get("parallel_name") or identity.get("parallel") or "").strip().casefold()
    return parallel not in {"", "base", "prizm", "refractor", "chrome"} and not (
        classification.field_matches.get("parallel") is True
        or classification.field_matches.get("visual_same_template_anchor") is True
    )


def enforce_formal_cardhobby_identity(
    identity: dict, provider_payload: dict | None, classification: Classification,
    *, visual_verdict: str | None = None, identity_rejected: bool = False,
    identity_accepted: bool = False, visual_review_required: bool = False,
) -> Classification:
    """Revalidate immutable source facts before any formal pricing path.

    Product analogues remain useful search leads, but their tier is not proof
    of the same card. Missing image anchors require an actual positive visual
    review; classifier/provider disagreement cannot authorize publication.
    """
    reasons = list(classification.hard_conflicts)
    if visual_review_required:
        reasons.append('stale_visual_requires_reverification')
    if requires_visual_confirmation("parallel", identity.get("parallel_name")) and not (
        visual_verdict == "same"
        and (classification.field_matches.get("visual_same_template_anchor") is True
             or (identity_accepted and not identity_rejected))
    ):
        # Descriptive seller vocabulary is not a checklist identity. A model
        # outage must not silently upgrade text-only recall into a same-card sale.
        reasons.append("descriptive_parallel_requires_visual_confirmation")
    if identity_rejected:
        reasons.append("current_identity_evidence_explicitly_rejected")
    if visual_verdict == "different":
        reasons.append("visual_identity_conflict")
    try:
        sale = CardHobbySale.model_validate(provider_payload).model_copy(
            update={"conflicts": [], "matching_fields": []}
        )
        reasons.extend(public_auction_rejection_reasons(
            occurred_at=sale.occurred_at, bid_count=sale.bid_count,
            sale_type=sale.sale_type, evidence_level=sale.evidence_level,
            fx_rate_to_base=sale.fx_rate_to_base,
        ))
        voucher_conflict = redemption_conflict(identity, sale.title)
        if voucher_conflict:
            reasons.append(voucher_conflict)
        if is_multi_card_listing(sale.title, identity.get("product_name")):
            reasons.append("multi_card_listing_not_single_card_evidence")
        if not identity.get("grade_company") and is_graded_listing(sale.title):
            reasons.append("raw_target_graded_comparable")
        if title_discloses_damage(sale.title):
            reasons.append("disclosed_condition_damage_requires_separate_valuation")
        if re.search(r"不保卡品|不保品相", sale.title):
            reasons.append("listing_condition_not_guaranteed")
        if title_requires_named_parallel(identity, sale.title):
            reasons.append("target_parallel_unknown_for_explicit_listing_variant")
        _, conflicts, tier = assess_cardhobby_candidate(identity, sale)
        # A persisted decision for the current identity may supply omitted
        # catalog fields. It never overrides explicit source contradictions.
        reviewed_missing_fields = {
            "标题未命中目标平行版本",
            "标题未命中目标卡号或卡种",
            "标题未命中目标卡号，基础/原生闪卡需图像确认",
        }
        if (
            identity_accepted and not identity_rejected
            and classification.tier in {ComparableTier.T0, ComparableTier.T1}
            and tier is not None and conflicts
            and all(reason in reviewed_missing_fields for reason in conflicts)
        ):
            tier, conflicts = "exact_or_near_exact", []
        # A seller's explicit season must agree even if an older provider
        # tolerates nearby releases. Exclude print-run denominators.
        years = {int(match.group(1)) for match in re.finditer(
            r"(?<![\d/])((?:19|20)\d{2})(?!\d|\s*[编遍])", sale.title
        )}
        target_years = {int(identity[key]) for key in ("season_start", "season_end")
                        if identity.get(key)}
        if years and target_years and not years.intersection(target_years):
            reasons.append("explicit_release_year_conflict")
        if tier == "exact_or_near_exact" and conflicts:
            reasons.extend(conflicts)
    except (TypeError, ValueError):
        conflicts, tier = [], None
        reasons.append("cardhobby_source_payload_not_verifiable")
    if tier == UNGRADED_SAME_CARD_BASIS:
        # This is an explicitly disclosed basis, not an exact-grade sale.
        # Reclassify the underlying card from immutable source facts. Only the
        # expected raw/graded state conflict can be replaced; visual, version,
        # condition and user rejection reasons remain decisive.
        underlying = CardIdentityResolver().resolve({
            **identity, "grade_company": None, "grade_value": None,
        })
        raw_classification = ComparableClassifier().classify(
            underlying,
            ComparableInput(
                title=sale.title,
                grade_company=sale.grade_company,
                grade_value=sale.grade_value,
                source_code="cardhobby",
                source_reliability=classification.source_reliability,
            ),
        )
        reasons = [reason for reason in reasons if reason != "grade_state_conflict"]
        reasons.extend(raw_classification.hard_conflicts)
        if raw_classification.tier not in {ComparableTier.T0, ComparableTier.T1}:
            reasons.append("ungraded_baseline_same_card_not_confirmed")
        # A grade-state rejection returns no field matches. Validate the
        # printed parallel against the newly verified underlying card instead.
        if _specific_parallel_unconfirmed(identity, raw_classification):
            reasons.append("specific_parallel_not_confirmed")
        if not reasons:
            return Classification(
                ComparableTier.T2,
                min(raw_classification.similarity, Decimal("0.55")),
                classification.source_reliability,
                field_matches={
                    **raw_classification.field_matches,
                    "grade": False,
                    UNGRADED_SAME_CARD_BASIS: True,
                    "formal_cardhobby_contract": FORMAL_CARDHOBBY_CONTRACT,
                },
            )
    elif tier == "image_review_candidate":
        if not (
            classification.tier in {ComparableTier.T0, ComparableTier.T1}
            and classification.field_matches.get("visual_same_template_anchor") is True
        ):
            reasons.append("cardhobby_same_card_visual_confirmation_pending")
    elif tier != "exact_or_near_exact":
        reasons.append("cardhobby_discovery_candidate_not_same_card_evidence")
        reasons.extend(conflicts)
    if tier != UNGRADED_SAME_CARD_BASIS and _specific_parallel_unconfirmed(identity, classification):
        reasons.append("specific_parallel_not_confirmed")
    fields = {**classification.field_matches,
              "formal_cardhobby_contract": FORMAL_CARDHOBBY_CONTRACT}
    if reasons:
        return Classification(
            ComparableTier.REJECTED, Decimal("0"), classification.source_reliability,
            field_matches=fields, hard_conflicts=list(dict.fromkeys(reasons)),
        )
    return Classification(
        classification.tier, classification.similarity, classification.source_reliability,
        field_matches=fields,
    )
