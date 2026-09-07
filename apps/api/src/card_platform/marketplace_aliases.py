"""Auditable seller vocabulary used by marketplace discovery.

This is deliberately a *candidate* catalog.  It is not a published knowledge
snapshot and callers must not treat an entry as an identity assertion until it
has been reviewed and published from the knowledge workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode


CARDHOBBY_SEARCH_URL = "https://cardhobby.com.cn/NewCommodity/SearchCommodity"


@dataclass(frozen=True, slots=True)
class MarketplaceAliasCandidate:
    entity_type: str
    canonical_name: str
    aliases: tuple[str, ...]
    note: str

    def evidence_url(self) -> str:
        # A reproducible public CardHobby search, not a claim that every result
        # is a matching sale.  Individual result URLs are retained by the
        # valuation evidence pipeline after identity gating.
        return f"{CARDHOBBY_SEARCH_URL}?{urlencode({'keyWord': self.aliases[1]})}"


# These entries are curated from repeatable public CardHobby seller searches.
# Additions belong here first, then are initialized as reviewable knowledge
# drafts with their query evidence; they never silently become live aliases.
INITIAL_MARKETPLACE_ALIAS_CATALOG: tuple[MarketplaceAliasCandidate, ...] = (
    MarketplaceAliasCandidate("player", "Magic Johnson", ("Magic Johnson", "魔术师约翰逊", "魔术师"), "球员中文昵称；与 Magicians insert 俗称重叠，身份门禁需按实体角色消歧。"),
    MarketplaceAliasCandidate("insert", "Stained Glass", ("Stained Glass", "教堂", "彩色玻璃"), "Mosaic 大比例彩窗特卡的中文卖家俗称。"),
    MarketplaceAliasCandidate("insert", "Finals MVPs", ("Finals MVPs", "捧杯", "总决赛MVP", "FMVP"), "卖家标题常用冠军奖杯简称。"),
    MarketplaceAliasCandidate("insert", "Bounce House", ("Bounce House", "跳跳屋", "弹跳屋"), "中文卖家简称。"),
    MarketplaceAliasCandidate("insert", "Jam Masters", ("Jam Masters", "扣篮大师", "大头特卡"), "中文卖家简称。"),
    MarketplaceAliasCandidate("insert", "Bomb Squad", ("Bomb Squad", "爆炸小队"), "中文卖家简称。"),
    MarketplaceAliasCandidate("insert", "Magicians", ("Magicians", "魔术师"), "中文卖家简称。"),
    MarketplaceAliasCandidate("insert", "Penmanship", ("Penmanship", "书法签"), "中文卖家简称。"),
    MarketplaceAliasCandidate("insert", "Jumbo Jersey Autographs", ("Jumbo Jersey Autographs", "大窗球衣签字", "JA 球衣签字"), "描述大窗球衣签字卡的卖家词。"),
    MarketplaceAliasCandidate("insert", "Fractals", ("Fractals", "Fractal", "分形", "分形特卡"), "Prizm 卖家标题常用单数英文或中文简称。"),
    MarketplaceAliasCandidate("insert", "Rookies III", ("Rookies III", "Rookies 3", "新秀三级", "三级"), "Court Kings 新秀层级的中英文卖家称呼。"),
    MarketplaceAliasCandidate("insert", "Topps Chrome Autographs", ("Topps Chrome Autographs", "新秀签", "TCA", "base签"), "Topps Chrome Update 基础新秀签在卡淘标题中的常见短称；仍需球员、产品、赛季和图像共同门禁。"),
    MarketplaceAliasCandidate("insert", "UD Game Floor", ("UD Game Floor", "Game Floor", "比赛地板", "地板"), "Upper Deck Hardcourt 比赛地板实物卡的卖家简称；仅在物料身份与产品门禁内使用。"),
    MarketplaceAliasCandidate("insert", "Silhouettes", ("Silhouettes", "Silhouette", "剪影", "剪影签"), "Preferred/Crown Royale 剪影球衣签的常见中英文简称。"),
    MarketplaceAliasCandidate("insert", "Treasured Threads", ("Treasured Threads", "球衣物料", "球衣", "物料"), "National Treasures Treasured Threads 的卖家描述词；必须结合产品、球员、赛季和限编。"),
    MarketplaceAliasCandidate("insert", "Star Cast", ("Star Cast", "胶片特卡", "胶片", "星星"), "Topps Signature Class 透明胶片 SSP insert 的常见标题词；短中文词只在球员、产品与图像门禁内使用。"),
    MarketplaceAliasCandidate("insert", "Hoopnotic", ("Hoopnotic", "Hoop-Notic", "迷幻特卡"), "2025-26 Topps NBA Hoops 大比例特卡；圆形版式会遮挡中间字母，标题常用完整 Hoopnotic。"),
    MarketplaceAliasCandidate("insert", "Rookie Focus", ("Rookie Focus", "Focus", "新秀聚焦", "聚焦特卡"), "Photogenic Rookie Focus 的标题常只保留 Focus/聚焦；短词只在球员、赛季和产品均匹配时作为 insert 证据。"),
    MarketplaceAliasCandidate("insert", "Legendary Tandems", ("Legendary Tandems", "传奇双人", "传奇双人特卡"), "Contenders Optic 双人传奇特卡的中文直译；仍需双球员、产品和赛季共同命中。"),
    MarketplaceAliasCandidate("parallel", "Basketball Refractor", ("Basketball Refractor", "球皮折", "篮球纹折"), "Topps Chrome 篮球表皮圆点纹平行的中文卖家俗称。"),
    MarketplaceAliasCandidate("parallel", "Mosaic Pink Fluorescent", ("Mosaic Pink Fluorescent", "粉马赛克折", "粉荧光马赛克"), "Mosaic /10 Pink Fluorescent 的中文卖家词；必须结合 /10 限编与产品门禁。"),
)


def seller_aliases(entity_type: str, canonical_name: object) -> tuple[str, ...]:
    """Return catalog aliases for a canonical identity without fuzzy matching."""
    normalized = str(canonical_name or "").casefold().strip()
    for candidate in INITIAL_MARKETPLACE_ALIAS_CATALOG:
        if candidate.entity_type == entity_type and candidate.canonical_name.casefold() == normalized:
            return candidate.aliases
    return ()
