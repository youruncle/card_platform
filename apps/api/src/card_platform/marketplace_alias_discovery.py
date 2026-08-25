from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

from card_platform.knowledge import normalize_knowledge_text


CARDHOBBY_HOST = "cardhobby.com.cn"


@dataclass(frozen=True, slots=True)
class AliasEvidenceSample:
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class AliasDiscoveryProposal:
    observed_alias: str
    canonical_name: str
    entity_type: str
    applicability: dict[str, object]
    samples: tuple[AliasEvidenceSample, ...]
    confidence: Decimal


class AliasDiscoveryError(ValueError):
    pass


def build_alias_proposal(
    *,
    observed_alias: str,
    canonical_name: str,
    entity_type: str,
    applicability: dict[str, object],
    samples: list[dict[str, str]],
) -> AliasDiscoveryProposal:
    """Validate public listing evidence without promoting it to runtime knowledge."""
    alias = observed_alias.strip()
    canonical = canonical_name.strip()
    if not alias or normalize_knowledge_text(alias) == normalize_knowledge_text(canonical):
        raise AliasDiscoveryError("候选别名必须非空且不同于规范名")
    if not applicability:
        raise AliasDiscoveryError("候选必须声明产品、赛季或卡种适用范围")

    accepted: list[AliasEvidenceSample] = []
    seen_urls: set[str] = set()
    for raw in samples:
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip()
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if host != CARDHOBBY_HOST or not parsed.path.lower().startswith("/market/item/"):
            raise AliasDiscoveryError("样本必须使用公开卡淘商品详情 URL")
        if normalize_knowledge_text(alias) not in normalize_knowledge_text(title):
            raise AliasDiscoveryError("每个样本标题都必须明确包含候选别名")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        accepted.append(AliasEvidenceSample(title=title, url=url))
    if len(accepted) < 2:
        raise AliasDiscoveryError("候选至少需要两个不同的公开卡淘样本")

    # Evidence volume improves confidence, but automated discovery never reaches
    # the 0.90 promotion threshold; a human replay/review remains mandatory.
    confidence = min(Decimal("0.89"), Decimal("0.65") + Decimal(len(accepted)) * Decimal("0.04"))
    return AliasDiscoveryProposal(
        observed_alias=alias,
        canonical_name=canonical,
        entity_type=entity_type,
        applicability=applicability,
        samples=tuple(accepted),
        confidence=confidence,
    )
