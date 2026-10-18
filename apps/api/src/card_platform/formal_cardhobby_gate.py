"""Final same-card admission, independent of discovery tiers and stale markers."""
import re
from decimal import Decimal

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.finish_evidence import VisualMatchFacts, finish_state
from card_platform.domain.auction_evidence import public_auction_rejection_reasons
from card_platform.domain.redemption import redemption_conflict
from card_platform.domain.product_identity import reviewed_parent_product_confirmation
from card_platform.domain.scoped_parallel_names import parallel_match_text
from card_platform.domain.comparables import (
    Classification, ComparableTier, ComparableClassifier, ComparableInput,
)
from card_platform.domain.grade_basis import UNGRADED_SAME_CARD_BASIS
from card_platform.ingestion.cardhobby import CardHobbySale, assess_cardhobby_candidate
from card_platform.domain.listing_title import is_graded_listing, is_multi_card_listing
from card_platform.marketplace_aliases import requires_visual_confirmation

from card_platform.purchase_source_roles import purchase_source_rejection_reasons, is_owned_purchase_source

from card_platform.domain.listing_condition import (
    title_discloses_damage, title_disclaims_card_condition, condition_rejection_reasons, disclose_minor_condition,
)

FORMAL_CARDHOBBY_CONTRACT = "same-card-source-revalidated-v21"


def _corroborated_same_card(identity, sale, observation, reasons, *, resolver=None):
    """Combine identity anchors and measured layout without claiming exact finish.

    This is a lower-weight same-card estimate. Missing finish/condition detail
    stays unknown; explicit identity, sale and damage contradictions still fail.
    """
    omissions = {
        'unknown_target_finish_requires_visual_confirmation',
        'bound_paired_finish_evidence_required', 'numbered_target_parallel_unconfirmed',
        'target_parallel_unknown_for_explicit_listing_variant',
        'cardhobby_same_card_visual_confirmation_pending',
        'minor_condition_requires_current_image_comparison',
        'stale_visual_requires_reverification',
    }
    if set(reasons) - omissions or not sale or not observation:
        return None
    from card_platform.comparable_image_verifier import (
        MODEL_OBSERVATION_CONTRACT, VisualCandidate, current_persisted_visual,
    )
    candidate = VisualCandidate(sale.source_url, sale.title, sale.image_url or '')
    current = current_persisted_visual(identity, candidate, observation)
    binding = current.get('visual_evidence_binding') or {}
    if (current.get('visual_verdict') not in {'same', 'compatible', 'uncertain'}
            or 'candidate_single_physical_card' not in current.get('visual_reasons', [])
            or binding.get('model_observation_contract') != MODEL_OBSERVATION_CONTRACT
            or not re.fullmatch(r'[0-9a-f]{64}', str(binding.get('candidate_sha256') or ''))):
        return None
    geometry = []
    for reason in current.get('visual_reasons') or []:
        match = re.fullmatch(r'geometry_same_template:matches=(\d+),inliers=(\d+),ratio=([0-9.]+)', reason)
        if match and int(match[1]) >= int(match[2]) >= 20 and Decimal('.65') <= Decimal(match[3]) <= 1:
            geometry.append(reason)
    if not geometry:
        return None
    if (current.get('visual_condition_review') or {}).get('verdict') == 'materially_different':
        return None
    # Recompute text anchors rather than trusting persisted classification flags
    # or treating an uncertain model's texture description as an identity field.
    text = ComparableClassifier(resolver).classify(CardIdentityResolver().resolve(identity),
        ComparableInput(title=sale.title, source_code='cardhobby',
                        grade_company=sale.grade_company, grade_value=sale.grade_value))
    fields = text.field_matches
    if text.hard_conflicts or not all(fields.get(key) is True for key in ('player', 'product', 'season', 'grade')):
        return None
    total = identity.get('serial_total') or identity.get('print_run')
    if total and fields.get('serial_total') is not True:
        return None
    if not (fields.get('card_number') is True or (total and fields.get('serial_total') is True)):
        return None
    from card_platform.domain.listing_condition import title_discloses_minor_condition
    return {
        **fields, 'corroborated_same_card': True,
        'same_card_basis': 'identity_anchors_and_current_image_template',
        'template_evidence': geometry,
        'finish_evidence_status': 'inferred_from_identity_and_template',
        'exact_finish_verified': False,
        'condition_basis': 'seller_disclosed_condition_not_fully_verified',
        'minor_condition_disclosed': title_discloses_minor_condition(sale.title),
        'corroborated_evidence_binding': binding,
        'valuation_limitations': ['平行版本由身份和卡面综合推断，未独立核实具体折射名称',
                                 '品相未完整核验，保留卖家披露及原成交价，不换算完美品相价'],
    }


def title_requires_named_parallel(identity: dict, title: str) -> bool:
    """A similar layout cannot fill an unknown target's version-bearing color.

    Equal print runs also occur across distinct finishes (solid/wave/shimmer).
    Generic foil wording is intentionally excluded because many base inserts
    have an intrinsic finish. Require a specific color/finish claim instead.
    """
    if str(identity.get("parallel_name") or identity.get("parallel") or "").strip():
        return False
    return bool(re.search(
        r"(?:红|紅|蓝|藍|绿|綠|青|金|银|銀|紫|黑|白|橙|粉|黄|黃)"
        r"(?:色)?\s*(?:波纹|波紋|波浪|碎冰|冰晶|闪光|閃光|亮片|格子|变形虫|變形蟲|熔岩)?\s*(?:折|平行)"
        r"|\b(?:gold|silver|red|blue|green|purple|black|white|orange|pink|yellow|aqua|teal)"
        r"[\s-]+(?:(?:wave|shimmer|ice|disco|pulsar|scope|laser)[\s-]+)?"
        r"(?:refractor|prizm|parallel)\b"
        r"|(?:中子|宇宙|星爆|银河|銀河)\s*(?:折|平行)"
        r"|\b(?:cosmic|galactic|neutron|supernova)\s+(?:parallel|refractor)\b",
        title, re.I,
    ))


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
    visual_facts: VisualMatchFacts | None = None, visual_observation: dict | None = None, resolver=None,
) -> Classification:
    """Revalidate immutable source facts before any formal pricing path.

    Exact visual confirmation or corroborated identity/template evidence can
    establish a same-card basis; the latter discloses unresolved finish/condition.
    """
    reasons = list(classification.hard_conflicts)
    reasons.extend(reason for reason in (visual_observation or {}).get('visual_reasons', [])
                   if reason in {'candidate_multiple_physical_cards', 'candidate_single_card_scope_unverified'})
    reasons.extend(purchase_source_rejection_reasons(identity, provider_payload))
    copyright_label = None
    paired_finish = bool(visual_facts and provider_payload and visual_facts.current_for(
        identity, provider_payload.get('source_url'), provider_payload.get('title'),
        provider_payload.get('image_url')))
    if not paired_finish:
        # Exact seller text is a discovery anchor, including for a known Base
        # or Refractor. It cannot establish which player, subset or finish is
        # actually photographed. Geometry alone also cannot supply that proof.
        # Keep this outside the corroborated-template omissions below.
        reasons.append('current_paired_card_image_confirmation_required')
    if (provider_payload and not paired_finish
            and parallel_match_text(str(provider_payload.get('title') or ''), identity, resolver=resolver)
                != str(provider_payload.get('title') or '')):
        # Recompute scoped terminology and exact current asset binding. A
        # persisted classification flag or stale same-image result is not
        # permission to use a market term as a named physical finish.
        reasons.append('scoped_parallel_name_requires_current_paired_finish')
    parallel_name = str(identity.get("parallel_name") or identity.get("parallel") or "").strip()
    if finish_state(identity) == 'unknown' and not paired_finish:
        # Empty recognition means unknown finish, not affirmative Base. Even a
        # seller's Base title cannot establish what is on the owned physical card.
        reasons.append("unknown_target_finish_requires_visual_confirmation")
        reasons.append('bound_paired_finish_evidence_required')
    if re.fullmatch(r"[A-Za-z]", parallel_name):
        # X/H alone are unresolved version shorthand and can accidentally match
        # merchant inventory codes such as X83. Require a confirmed full name.
        reasons.append("target_parallel_abbreviation_unconfirmed")
    if identity.get("serial_total") and not paired_finish and not str(
        identity.get("parallel_name") or identity.get("parallel") or ""
    ).strip():
        # A print-run denominator is shared by different colors/textures. A
        # seller omitting the variant must not bypass the named-variant guard.
        reasons.append("numbered_target_parallel_unconfirmed")
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
    sale = None
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
        reasons.extend(condition_rejection_reasons(identity, sale.title, visual_facts,
            source_url=sale.source_url, image_url=sale.image_url))
        if title_requires_named_parallel(identity, sale.title) and not paired_finish:
            reasons.append("target_parallel_unknown_for_explicit_listing_variant")
        _, conflicts, tier = (assess_cardhobby_candidate(identity, sale, resolver=resolver)
                              if resolver is not None else assess_cardhobby_candidate(identity, sale))
        from card_platform.domain.reviewed_source_back import reviewed_source_back_omissions, reviewed_front_serial_digest
        from card_platform.domain.reviewed_catalog_back import reviewed_cover_subject, reviewed_copyright_year_label
        if (paired_finish and reviewed_cover_subject(identity, sale.title, visual_facts, resolver=resolver)
                and conflicts and set(conflicts) <= {
                    '清单作品名称未在标题中完整出现，需图像确认', '目标表面版本未知，需图像确认'}):
            tier, conflicts = 'image_review_candidate', []
        if (paired_finish and visual_facts._reviewed_proof is not None
                and classification.tier in {ComparableTier.T0, ComparableTier.T1}
                and classification.field_matches.get('reviewed_named_finish_omission') is True
                and conflicts and set(conflicts) <= (
                    {'标题未命中目标平行版本'} | set(reviewed_source_back_omissions(
                        CardIdentityResolver().resolve(identity), sale.title, visual_facts)))):
            # The in-process seal already comes from a current accepted paired
            # original review. Search admission and final pricing must consume
            # that same authority, even before a separate identity flag exists.
            # Explicit source conflicts and all price/condition checks remain.
            tier, conflicts = 'image_review_candidate', []
        if (paired_finish and reviewed_front_serial_digest(
                CardIdentityResolver().resolve(identity), sale.title, visual_facts)
                and conflicts and set(conflicts) <= {
                    '目标为限编卡，但标题未命中目标总限编', '目标表面版本未知，需图像确认'}):
            tier, conflicts = 'image_review_candidate', []
        if (paired_finish and conflicts and set(conflicts) <= reviewed_source_back_omissions(
                CardIdentityResolver().resolve(identity), sale.title, visual_facts)):
            tier, conflicts = "image_review_candidate", []
        if (paired_finish and reviewed_parent_product_confirmation(
                sale.title, identity, visual_facts, resolver=resolver)
                and conflicts and set(conflicts) <= ({
                    "标题未提供可验证的目标产品信息", "目标表面版本未知，需图像确认"}
                    | set(reviewed_source_back_omissions(
                        CardIdentityResolver().resolve(identity), sale.title, visual_facts)))):
            # Actual source facts remain untouched; both omissions are supplied
            # by the current sealed paired images and scoped official card row.
            tier, conflicts = "image_review_candidate", []
        from card_platform.domain.reviewed_reference_product import reviewed_reference_product_evidence
        if (paired_finish and classification.tier in {ComparableTier.T0, ComparableTier.T1}
                and reviewed_reference_product_evidence(sale.title, identity, visual_facts, resolver=resolver)
                and conflicts and set(conflicts) <= {
                    "标题未提供可验证的目标产品信息", "目标表面版本未知，需图像确认",
                    "标题未命中目标卡号或卡种", "标题未命中目标卡种，需图像确认"}):
            # Recompute current reference + sealed originals. Neither saved
            # field_matches nor reference research candidates alone grant this.
            tier, conflicts = "image_review_candidate", []
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
        from card_platform.domain.entity_roles import title_without_catalog_subject
        attribute_title = title_without_catalog_subject(sale.title, identity, resolver=resolver)
        years = {int(match.group(1)) for match in re.finditer(
            r"(?<![\d/])((?:19|20)\d{2})(?!\d|\s*[编遍])", attribute_title
        )}
        target_years = {int(identity[key]) for key in ("season_start", "season_end")
                        if identity.get(key)}
        if years and target_years and not years.intersection(target_years):
            copyright_label = reviewed_copyright_year_label(identity, sale.title, visual_facts, resolver=resolver)
            if not copyright_label:
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
        raw_classification = ComparableClassifier(resolver=resolver).classify(
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
    if is_owned_purchase_source(identity, provider_payload):
        fields['owned_public_auction_history'] = True
    if copyright_label:
        fields['reviewed_copyright_year_label'] = copyright_label
    corroborated = _corroborated_same_card(identity, sale, visual_observation, reasons, resolver=resolver)
    if corroborated and (reasons or classification.tier not in {ComparableTier.T0, ComparableTier.T1}):
        return Classification(ComparableTier.T1, Decimal('.78'),
            min(classification.source_reliability, Decimal('.55')),
            field_matches={**fields, **corroborated})
    if reasons:
        return Classification(
            ComparableTier.REJECTED, Decimal("0"), classification.source_reliability,
            field_matches=fields, hard_conflicts=list(dict.fromkeys(reasons)),
        )
    return disclose_minor_condition(Classification(
        classification.tier, classification.similarity, classification.source_reliability,
        field_matches=fields,
    ), str((provider_payload or {}).get('title') or ''))
