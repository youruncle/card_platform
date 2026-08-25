from decimal import Decimal

import pytest

from card_platform.marketplace_alias_discovery import AliasDiscoveryError, build_alias_proposal


def test_builds_scoped_review_required_cardhobby_alias_proposal() -> None:
    proposal = build_alias_proposal(
        observed_alias="教堂",
        canonical_name="Stained Glass",
        entity_type="insert",
        applicability={"product": "Mosaic", "season_start": 2020, "season_end": 2021},
        samples=[
            {
                "title": "2020-21 Mosaic LeBron James 教堂 特卡",
                "url": "https://cardhobby.com.cn/Market/Item/1001",
            },
            {
                "title": "詹姆斯 2020 Mosaic 教堂 Stained Glass",
                "url": "https://cardhobby.com.cn/Market/Item/1002",
            },
        ],
    )

    assert proposal.observed_alias == "教堂"
    assert proposal.applicability["product"] == "Mosaic"
    assert proposal.confidence == Decimal("0.73")
    assert proposal.confidence < Decimal("0.90")


@pytest.mark.parametrize(
    "samples",
    [
        [
            {"title": "Mosaic 教堂", "url": "https://example.com/Market/Item/1"},
            {"title": "Mosaic 教堂", "url": "https://cardhobby.com.cn/Market/Item/2"},
        ],
        [
            {"title": "Mosaic Stained Glass", "url": "https://cardhobby.com.cn/Market/Item/1"},
            {"title": "Mosaic 教堂", "url": "https://cardhobby.com.cn/Market/Item/2"},
        ],
    ],
)
def test_rejects_unverifiable_alias_samples(samples: list[dict[str, str]]) -> None:
    with pytest.raises(AliasDiscoveryError):
        build_alias_proposal(
            observed_alias="教堂",
            canonical_name="Stained Glass",
            entity_type="insert",
            applicability={"product": "Mosaic"},
            samples=samples,
        )
