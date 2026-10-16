"""Skip image work for source facts already disallowed by formal admission."""

from card_platform.domain.auction_evidence import public_auction_rejection_reasons
from card_platform.domain.listing_title import is_multi_card_listing
from card_platform.domain.listing_condition import condition_rejection_reasons
from card_platform.ingestion.cardhobby import CardHobbySale
from card_platform.purchase_source_roles import purchase_source_rejection_reasons


def visual_source_rejection_reasons(identity: dict, provider_payload: dict) -> list[str]:
    """Only immutable public-source guards; never infer visual identity failures.

    Malformed/missing payloads stay with the normal source-validation path.
    This optimization cannot admit a price and leaves each original comparable
    intact for the formal gate to apply its full set of checks.
    """
    try:
        sale = CardHobbySale.model_validate(provider_payload)
    except (TypeError, ValueError):
        return []
    reasons = public_auction_rejection_reasons(
        occurred_at=sale.occurred_at, bid_count=sale.bid_count,
        sale_type=sale.sale_type, evidence_level=sale.evidence_level,
        fx_rate_to_base=sale.fx_rate_to_base,
    )
    reasons.extend(purchase_source_rejection_reasons(identity, provider_payload))
    if is_multi_card_listing(sale.title, identity.get('product_name')):
        reasons.append('multi_card_listing_not_single_card_evidence')
    reasons.extend(condition_rejection_reasons(identity, sale.title, discovery=True))
    return reasons
