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
    MarketplaceAliasCandidate("insert", "Stained Glass", ("Stained Glass", "教堂", "彩色玻璃"), "Mosaic 大比例彩窗特卡的中文卖家俗称。"),
    MarketplaceAliasCandidate("insert", "Finals MVPs", ("Finals MVPs", "捧杯", "总决赛MVP", "FMVP"), "卖家标题常用冠军奖杯简称。"),
    MarketplaceAliasCandidate("insert", "Bounce House", ("Bounce House", "跳跳屋", "弹跳屋"), "中文卖家简称。"),
    MarketplaceAliasCandidate("insert", "Jam Masters", ("Jam Masters", "扣篮大师", "大头特卡"), "中文卖家简称。"),
    MarketplaceAliasCandidate("insert", "Bomb Squad", ("Bomb Squad", "爆炸小队"), "中文卖家简称。"),
    MarketplaceAliasCandidate("insert", "Magicians", ("Magicians", "魔术师"), "中文卖家简称。"),
    MarketplaceAliasCandidate("insert", "Penmanship", ("Penmanship", "书法签"), "中文卖家简称。"),
    MarketplaceAliasCandidate("insert", "Jumbo Jersey Autographs", ("Jumbo Jersey Autographs", "大窗球衣签字", "JA 球衣签字"), "描述大窗球衣签字卡的卖家词。"),
)


def seller_aliases(entity_type: str, canonical_name: object) -> tuple[str, ...]:
    """Return catalog aliases for a canonical identity without fuzzy matching."""
    normalized = str(canonical_name or "").casefold().strip()
    for candidate in INITIAL_MARKETPLACE_ALIAS_CATALOG:
        if candidate.entity_type == entity_type and candidate.canonical_name.casefold() == normalized:
            return candidate.aliases
    return ()
