from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from card_platform.ingestion.cardhobby import (
    CardHobbyProvider,
    CardHobbySale,
    assess_cardhobby_candidate,
)
from card_platform.marketplace_aliases import INITIAL_MARKETPLACE_ALIAS_CATALOG, seller_aliases


def test_stained_glass_church_term_has_auditable_catalog_entry() -> None:
    candidate = next(item for item in INITIAL_MARKETPLACE_ALIAS_CATALOG if item.canonical_name == "Stained Glass")
    assert candidate.entity_type == "insert"
    assert "教堂" in candidate.aliases
    assert candidate.evidence_url().startswith("https://cardhobby.com.cn/")


def test_cardhobby_reads_shared_marketplace_alias_catalog() -> None:
    assert seller_aliases("insert", "Stained Glass") == ("Stained Glass", "教堂", "彩色玻璃")
    queries = CardHobbyProvider.build_queries(
        {"player_name": "LeBron James", "product_name": "Mosaic", "insert_name": "Stained Glass", "season_start": 2020, "season_end": 2021}
    )
    assert any("教堂" in query for query in queries)


def test_numeric_card_number_cannot_promote_explicit_base_to_target_insert() -> None:
    sale = CardHobbySale(
        item_id="128370328",
        title=(
            "2020-21 Panini Mosaic LeBron James 马赛克 MVP 捧杯 base "
            "微瑕如图(1)"
        ),
        source_url="https://cardhobby.com.cn/Market/Item/128370328",
        occurred_at=datetime(2026, 8, 16, tzinfo=ZoneInfo("Asia/Shanghai")),
        sale_price=Decimal("6.3"),
        bid_count=6,
        sale_type="auction",
        evidence_level="ended_auction",
        sold_status_evidence="卡淘已售接口 Status=-2，出价 6 次",
        match_confidence=Decimal("0.95"),
    )
    score, conflicts, tier = assess_cardhobby_candidate(
        {
            "player_name": "LeBron James",
            "season_start": 2020,
            "season_end": 2021,
            "product_name": "Mosaic Basketball",
            "card_number": "1",
            "insert_name": "Stained Glass",
            "is_autograph": False,
            "is_patch": False,
        },
        sale,
    )

    assert score == Decimal("0")
    assert tier is None
    assert "标题明确为基础卡，与目标卡种不一致" in conflicts
    assert "标题未命中目标卡号或卡种" in conflicts
