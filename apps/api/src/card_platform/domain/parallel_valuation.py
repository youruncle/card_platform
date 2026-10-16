"""Auditable, low-confidence same-base-card / different-print-run fallback.

The square-root multiplier is a bounded policy prior, NOT a fitted market
premium. Exact-sale admission is deliberately kept outside this module.
"""
import re
from decimal import Decimal

from card_platform.domain.auction_evidence import public_auction_rejection_reasons
from card_platform.domain.comparables import ComparableTier
from card_platform.domain.listing_condition import (
    condition_rejection_reasons, title_discloses_minor_condition,
)
from card_platform.domain.valuation_quality import ShadowSale, ShadowValuationEstimator, identity_review_reasons

PARALLEL_BASIS = 'same_base_different_print_run'
PARALLEL_MODEL = 'bounded-sqrt-print-run-prior-v1'
_ALLOWED_DIFFERENCES = {
    '明确限编与目标卡不一致', '标题明确为其他平行版本，与目标平行不一致',
    '标题为比目标更具体的其他平行版本', '标题未命中目标平行版本',
}


def assess_parallel_analogue(identity, sale, *, resolver=None):
    """Discovery qualification only. No title/identity edits or fake exact match."""
    from card_platform.ingestion.cardhobby import assess_cardhobby_match, _explicit_print_runs, _explicit_release_years
    from card_platform.domain.product_identity import assess_identity_product_match, ProductMatchStatus
    from card_platform.domain.season import explicit_seasons as parse_explicit_seasons
    from card_platform.domain.sketch_identity import is_sketch_card
    from card_platform.purchase_source_roles import purchase_source_rejection_reasons
    if identity_review_reasons(identity) or not identity.get('card_number'):
        return None
    if is_sketch_card(identity.get('insert_name') or identity.get('insert') or '') or is_sketch_card(sale.title):
        return None
    try:
        target = int(identity.get('serial_total') or identity.get('print_run') or 0)
        totals = _explicit_print_runs(sale.title)
        if len(totals) != 1:
            return None
        source = next(iter(totals))
        if min(target, source) <= 1 or target == source or max(target, source) / min(target, source) > 10:
            return None
        if not sale.sale_price.is_finite() or sale.sale_price <= 0:
            return None
        if public_auction_rejection_reasons(occurred_at=sale.occurred_at, bid_count=sale.bid_count,
                sale_type=sale.sale_type, evidence_level=sale.evidence_level, fx_rate_to_base=sale.fx_rate_to_base):
            return None
    except (TypeError, ValueError, ArithmeticError):
        return None
    if not re.fullmatch(r'https://(?:www\.)?cardhobby\.com\.cn/[Mm]arket/[Ii]tem/' + re.escape(str(sale.item_id)), sale.source_url):
        return None
    if purchase_source_rejection_reasons(identity, sale.model_dump(mode='json')):
        return None
    if condition_rejection_reasons(identity, sale.title, discovery=True):
        return None
    seasons = parse_explicit_seasons(sale.title)
    years = _explicit_release_years(sale.title)
    if not ((identity.get('season_start'), identity.get('season_end')) in seasons
            or not seasons and identity.get('season_start') in years):
        return None
    if assess_identity_product_match(sale.title, identity, resolver=resolver) != ProductMatchStatus.MATCH:
        return None
    # Stored matcher diagnostics are derived, not immutable sale facts.
    clean_sale = sale.model_copy(update={'conflicts': [], 'matching_fields': []})
    _, differences = assess_cardhobby_match(identity, clean_sale, resolver=resolver)
    differences = list(dict.fromkeys(differences))
    if not differences or set(differences) - _ALLOWED_DIFFERENCES:
        return None
    return {'source_serial_total': source, 'target_serial_total': target,
            'target_parallel': identity.get('parallel_name'), 'source_title': sale.title,
            'differences': differences, 'pricing_basis': PARALLEL_BASIS}


def _base_card_evidence(identity, sale, observation):
    from card_platform.domain.comparables import _contains_card_number
    from card_platform.comparable_image_verifier import (
        VisualCandidate, current_persisted_visual, MODEL_OBSERVATION_CONTRACT,
        visual_asset_binding, has_visual_observation,
    )
    candidate = VisualCandidate(sale.source_url, sale.title, sale.image_url or '')
    current = current_persisted_visual(identity, candidate, observation or {})
    if (current.get('visual_condition_review') or {}).get('verdict') == 'materially_different':
        return None
    # Explicit card number plus all strict non-variant fields is sufficient
    # for a low-confidence analogue. Geometry is required when it is omitted.
    number_text = re.sub(r'(?<![0-9a-z])\d+\s*(?:/|of)\s*\d+(?![0-9a-z])', ' ', sale.title, flags=re.I)
    number = str(identity['card_number']).strip().lstrip('#')
    if number.isascii() and number.isdigit():
        # A zero-padded numerator or N编 is still a print run, never a card #.
        number_match = bool(re.search(
            r'(?:#|\bno\.?\s*|\bcard\s*#?\s*|卡[号號]\s*)0*' + re.escape(str(int(number)))
            + r'(?![0-9a-z]|\s*(?:[编編]|of|/))', number_text, re.I))
    else:
        number_match = _contains_card_number(number_text, number)
    if number_match:
        return {'base_card_basis': 'title_card_number_and_identity'}
    binding = current.get('visual_evidence_binding') or {}
    hashes = identity.get('image_content_hashes') or {}
    expected = visual_asset_binding(identity, candidate, b'', None, b'')
    if (not has_visual_observation(current) or not hashes.get('front')
            or binding.get('target_front_sha256') != hashes.get('front')
            or binding.get('target_back_sha256') != hashes.get('back')
            or identity.get('image_back_url') and not hashes.get('back')
            or any(binding.get(key) != expected[key] for key in (
                'contract', 'identity_sha256', 'candidate_title_sha256', 'source_url', 'candidate_image_url'))):
        return None
    if binding.get('model_observation_contract') != MODEL_OBSERVATION_CONTRACT:
        return None
    if not re.fullmatch(r'[0-9a-f]{64}', str(binding.get('candidate_sha256') or '')):
        return None
    for reason in current.get('visual_reasons') or []:
        match = re.fullmatch(r'geometry_same_template:matches=(\d+),inliers=(\d+),ratio=([0-9.]+)', reason)
        if match and int(match[1]) >= int(match[2]) >= 20 and Decimal('.65') <= Decimal(match[3]) <= 1:
            # A different finish is expected here. This does not grant an exact
            # visual verdict, finish equivalence or a perfect-condition claim.
            return {'base_card_basis': 'identity_and_current_artwork_geometry',
                    'template_evidence': reason, 'evidence_binding': binding}
    return None


def estimate_parallel_fallback(identity, rows, exact_estimate, *, resolver=None):
    """Return exact result untouched, otherwise estimate only qualified analogues."""
    if exact_estimate.fair is not None:
        return exact_estimate, {}
    from card_platform.ingestion.cardhobby import CardHobbySale
    sales, details, seen = [], {}, set()
    for row in rows:
        if row.get('source_code') != 'cardhobby' or row.get('reviewed_identity_rejected'):
            continue
        try:
            sale = CardHobbySale.model_validate(row.get('provider_raw_payload'))
        except (TypeError, ValueError):
            continue
        if sale.item_id in seen or sale.source_url != row.get('source_url') or sale.title != row.get('listing_title'):
            continue
        facts = assess_parallel_analogue(identity, sale, resolver=resolver)
        if not facts:
            continue
        base = _base_card_evidence(identity, sale, row.get('raw_payload'))
        if not base:
            continue
        original = sale.sale_price * sale.fx_rate_to_base
        if not original.is_finite() or original != row.get('value_base'):
            continue
        multiplier = min(Decimal('2'), max(Decimal('.5'),
            (Decimal(facts['source_serial_total']) / Decimal(facts['target_serial_total'])).sqrt()))
        adjusted = original * multiplier
        key = str(row['id'])
        sales.append(ShadowSale(key, 'cardhobby', adjusted, Decimal('0'), row['age_days'],
            ComparableTier.T2, Decimal('.60'), Decimal('.55'), sale.evidence_level, PARALLEL_BASIS))
        seen.add(sale.item_id)
        details[key] = {**facts, **base, 'model': PARALLEL_MODEL, 'market_calibrated': False,
            'formula': 'reference_price * clamp(sqrt(reference_total / target_total), 0.5, 2)',
            'multiplier': str(multiplier), 'original_price': str(original),
            'adjusted_price': str(adjusted.quantize(Decimal('.01'))),
            'source_url': sale.source_url, 'minor_condition_disclosed': title_discloses_minor_condition(sale.title),
            'condition_adjustment_applied': False, 'exact_version_verified': False}
    if not sales:
        return exact_estimate, {}
    from dataclasses import replace
    estimate = ShadowValuationEstimator().estimate(sales, identity)
    used = {key: details[key] for key in estimate.used_ids}
    # Include the observed anchor as well as a deliberately broad prior range.
    low = min(estimate.low, estimate.fair * Decimal('.45'), *(Decimal(v['original_price']) for v in used.values()))
    high = max(estimate.high, estimate.fair * Decimal('1.65'), *(Decimal(v['original_price']) for v in used.values()))
    return replace(estimate, low=low.quantize(Decimal('.01')), high=high.quantize(Decimal('.01')),
        confidence=min(estimate.confidence, Decimal('.18')),
        review_reasons=tuple(dict.fromkeys([*estimate.review_reasons, PARALLEL_BASIS,
            'print_run_multiplier_is_uncalibrated_prior', 'parallel_and_condition_premium_uncertain'])),
        rejected=(*exact_estimate.rejected, *estimate.rejected)), used
