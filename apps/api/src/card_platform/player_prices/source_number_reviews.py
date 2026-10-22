"""Approved, single-source number roles; title markers are not seller identity."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from card_platform.domain.listing_title import has_card_number_declaration

from card_platform.ingestion.cardhobby import _explicit_hash_card_numbers, _normalized_card_number
from card_platform.player_prices.discovery import PRODUCTS
from card_platform.public_source_catalog import validated_sale

EXPLICIT_MULTI_LOT = re.compile(
    r"(?<!\d)(?:[2-9]|[1-9]\d)\s*张\s*(?:lot|打包|同出)|"
    r"(?:共|合售)\s*(?:[2-9]|[1-9]\d)\s*张|"
    r"\b(?:[2-9]|[1-9]\d)\s+cards?\s+(?:lot|bundle)\b",
    re.I,
)


class SourceNumberRoleReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: Literal["price-source-number-role-v1"]
    code: str = Field(pattern=r"^[A-Za-z]{1,8}\d{1,8}$")
    seller_title_marker: str = Field(pattern=r"^【[^【】\r\n]{2,60}】$")
    title_start: int = Field(ge=0)
    title_end: int = Field(ge=1)
    detail: str = Field(min_length=40, max_length=1200)
    supporting_observations: list[dict] = Field(min_length=3, max_length=20)


def _suffix(title, role):
    match = re.search(r"(?<![A-Za-z0-9#])#\s*" + re.escape(role.code) + r"\s*$", title)
    if not match or title.count(role.seller_title_marker) != 1:
        raise ValueError("source_number_role_context_missing")
    if has_card_number_declaration(title):
        raise ValueError("explicit_collector_number_cannot_be_reclassified")
    return match.start(), len(title.rstrip())


def _positive_multi_lot(title):
    view = re.sub(
        r"(?:并不是|不是|并非|並非|\bnot\b|非(?=\s*\d))[^，。；;,.]*", "", title, flags=re.I
    )
    return any(
        not re.search(r"第\s*$", view[: m.start()]) for m in EXPLICIT_MULTI_LOT.finditer(view)
    )


def validate_source_number_role(observation, role, reviewed_at, card_number):
    """Caller separately authenticates the enclosing immutable review hash.

    Public rows support the operator's explicit judgement; their count and a
    repeated title marker cannot themselves authorize a number exemption.
    """
    sale, _ = validated_sale(observation)
    span = _suffix(sale.title, role)
    code = _normalized_card_number(role.code)
    if (
        span != (role.title_start, role.title_end)
        or not card_number
        or code == _normalized_card_number(card_number)
        or _explicit_hash_card_numbers(sale.title) != {code}
    ):
        raise ValueError("source_number_role_span_or_purpose_changed")
    sources, hashes, products = [], [], set()
    lots = 0
    for observation in role.supporting_observations:
        peer, captured = validated_sale(observation)
        if peer.item_id == sale.item_id or peer.item_id in sources or captured > reviewed_at:
            raise ValueError("source_number_role_support_unbound")
        _suffix(peer.title, role)
        sources.append(peer.item_id)
        hashes.append(observation["raw_row_sha256"])
        if _positive_multi_lot(peer.title):
            lots += 1
            peer_products = set()
            for product, aliases in PRODUCTS.items():
                if any(
                    re.search(
                        r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])", peer.title, re.I
                    )
                    for alias in aliases
                ):
                    peer_products.add(product)
            if "Donruss Optic" in peer_products:
                peer_products.discard("Donruss")
            products.update(peer_products)
    if lots < 3 or len(products) < 2:
        raise ValueError("source_number_role_support_insufficient")
    return dict(
        contract=role.contract,
        code=role.code,
        seller_title_marker=role.seller_title_marker,
        marker_is_title_context_not_authenticated_seller=True,
        title_span=list(span),
        supporting_source_ids=["item:" + s for s in sources],
        supporting_observation_sha256s=hashes,
        detail=role.detail,
    )
