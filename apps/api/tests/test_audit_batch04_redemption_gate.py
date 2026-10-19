from decimal import Decimal
import pytest
from card_platform.domain.comparables import Classification, ComparableTier
from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity
from card_platform.ingestion.cardhobby import CardHobbyProvider


@pytest.mark.parametrize('target_status,sale_status,unverified', [
    ('unknown', '', True),
    ('unknown', 'Unredeemed ', True),
    ('unredeemed', '', True),
    ('unredeemed', 'Unredeemed ', False),
])
def test_formal_voucher_price_requires_known_usage_state_for_both_objects(target_status,sale_status,unverified):
    # Source-normalization fixture; this is not a real provider/model receipt.
    sale = CardHobbyProvider.parse_market_item(dict(
        ID=123, Title=f'2025-26 Topps Inception Player RJ-KM {sale_status}Redemption',
        Status=-2, OutCount=2, PriceCount=2, ByWay=2, LowestPrice='28.50',
        EffectiveDate='2026-10-01 20:00:00',
    ))
    identity = dict(player_name='Player',brand_name='Topps',product_name='Inception',
                    season_start=2025,season_end=2026,card_number='RJ-KM',
                    is_redemption=True,redemption_status=target_status)
    result = enforce_formal_cardhobby_identity(
        identity,sale.model_dump(mode='json'),
        Classification(ComparableTier.T0,Decimal('1'),Decimal('.7')),
    )
    assert ('redemption_usage_status_unverified' in result.hard_conflicts) == unverified
