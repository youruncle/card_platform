import pytest

from card_platform.domain.listing_attributes import title_claims_autograph
from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import ComparableClassifier, ComparableInput
from card_platform.ingestion.cardhobby import CardHobbySale, assess_cardhobby_candidate


@pytest.mark.parametrize("claim", ["印刷签名", "印刷签字", "印签", "机签", "无签名", "非亲签", "不是亲笔", "printed autograph", "facsimile signature", "autograph is printed", "not signed", "non-auto", "without autograph"])
def test_printed_and_negated_signature_claims_are_not_autographs(claim):
    assert not title_claims_autograph("1999 Skybox Example Player " + claim)


@pytest.mark.parametrize("claim", ["亲笔签名", "卡签", "贴签", "on-card auto", "hand-signed", "autograph", "RPA", "印刷签名，另附真实卡签"])
def test_real_autograph_claims_remain_positive(claim):
    assert title_claims_autograph("Example Player " + claim)


def test_provider_and_classifier_agree_about_printed_signature():
    identity = dict(player_name="Chauncey Billups", season_start=1999, season_end=2000,
                    brand_name="Skybox", product_name="Dominion", card_number="104",
                    is_autograph=False, is_patch=False)
    title = "1999-00 Skybox Dominion Chauncey Billups #104 印刷签名"
    sale = CardHobbySale(item_id="123456", title=title,
                        source_url="https://cardhobby.com.cn/Market/Item/123456",
                        occurred_at="2026-09-01T10:00:00+08:00", sale_price="3", bid_count=2,
                        sale_type="auction", evidence_level="ended_auction",
                        sold_status_evidence="Fixture ended auction Status=-2, 2 bids", match_confidence="0.9")
    _, conflicts, _ = assess_cardhobby_candidate(identity, sale)
    assert "签字属性与目标卡不一致" not in conflicts
    classified = ComparableClassifier().classify(CardIdentityResolver().resolve(identity), ComparableInput(title=title))
    assert "autograph_conflict" not in classified.hard_conflicts


def test_a_real_autograph_target_must_not_use_printed_signature_prices():
    target = CardIdentityResolver().resolve(dict(player_name="Example Person", season_start=2000, product_name="Example", is_autograph=True))
    classified = ComparableClassifier().classify(target, ComparableInput(title="2000 Example Example Person printed autograph"))
    assert "autograph_conflict" in classified.hard_conflicts
