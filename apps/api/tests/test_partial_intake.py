import pytest
from card_platform.market_data.models import MarketIdentity


@pytest.mark.parametrize('flag',['is_rookie','is_autograph','is_memorabilia','is_patch'])
def test_unknown_market_attribute_is_distinct_from_known_false(flag):
    unknown = MarketIdentity(player_name='Test', **{flag:None})
    negative = MarketIdentity(player_name='Test', **{flag:False})
    assert getattr(unknown,flag) is None
    assert flag not in unknown.normalized()
    assert flag in negative.normalized()
    assert unknown.fingerprint != negative.fingerprint
