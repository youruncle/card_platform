from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from card_platform.domain.identity_review import ConfirmedIdentity
from card_platform.domain.valuation_quality import identity_review_reasons


class ResearchStatus(StrEnum):
    REQUIRED = "identity_research_required"
    CANDIDATE_READY = "candidate_identity_ready"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED_NO_EXACT_SALES = "confirmed_but_no_exact_sales"
    SHADOW_ESTIMATED = "shadow_estimated"


@dataclass(frozen=True, slots=True)
class ParallelDefinition:
    name: str
    serial_total: int
    visual_tokens: tuple[str, ...]
    channels: tuple[str, ...]


TOPPS_2025_FLAGSHIP_PARALLELS = (
    ParallelDefinition("Gold Diamante", 50, ("gold", "faceted", "diamante", "cracked ice"), ("hanger",)),
    ParallelDefinition("Gold Rainbow", 50, ("gold", "rainbow foil", "smooth"), ("hobby", "jumbo")),
    ParallelDefinition("Gold Holo Foil", 50, ("gold", "holographic", "smooth"), ("blaster", "mega")),
    ParallelDefinition("Season Tip-Off Gold Foilboard", 50, ("gold", "foilboard", "season tip off"), ("blaster",)),
    ParallelDefinition("Crackleboard Gold", 50, ("gold", "crackleboard", "cracked grid"), ("costco super box",)),
)

CHECKLIST_VERSION = "topps-2025-26-flagship-parallels-2026-08-12"
CHECKLIST_SOURCE = "https://www.beckett.com/news/2025-26-topps-basketball-cards/"


def candidate_search_queries(identity: dict[str, Any]) -> list[str]:
    season = f"{identity['season_start']}-{str(identity.get('season_end') or '')[-2:]}"
    return [
        f'"{identity["player_name"]}" "{identity["card_number"]}" "{identity["parallel_name"]}" /{identity["serial_total"]} {season} sold',
        f'"{identity["player_name"]}" "{identity["parallel_name"]}" completed sale',
    ]


def validate_candidate(candidate: dict[str, Any]) -> tuple[bool, list[str]]:
    try:
        ConfirmedIdentity.model_validate(candidate)
    except ValueError as error:
        return False, [str(error)]
    reasons = identity_review_reasons(candidate)
    return not reasons, reasons


def generate_candidates(
    current: dict[str, Any], visual: dict[str, Any]
) -> list[dict[str, Any]]:
    """Deterministic candidate generator fed by structured OCR/vision observations."""
    serial_total = visual.get("serial_total") or current.get("serial_total")
    tokens = {str(token).casefold() for token in visual.get("texture_tokens", [])}
    definitions = [item for item in TOPPS_2025_FLAGSHIP_PARALLELS if item.serial_total == serial_total]
    scored = []
    for definition in definitions:
        overlap = sum(any(token in observed or observed in token for observed in tokens)
                      for token in definition.visual_tokens)
        confidence = min(Decimal("0.96"), Decimal("0.58") + Decimal(overlap) * Decimal("0.11"))
        identity = {
            **current,
            "season_start": visual.get("product_season_start", current.get("season_start")),
            "season_end": visual.get("product_season_end", current.get("season_end")),
            "parallel_name": definition.name,
            "serial_number": visual.get("serial_number", current.get("serial_number")),
            "serial_total": serial_total,
            "set_name": current.get("set_name") or "Base",
            "insert_name": None,
        }
        valid, reasons = validate_candidate(identity)
        scored.append({
            "identity": identity,
            "confidence": str(confidence),
            "is_valid": valid,
            "validation_reasons": reasons,
            "visual_reasons": visual.get("reasons", []),
            "field_provenance": {
                "season_start": [{"source": "card_back_year_and_stats", "value": identity["season_start"]}],
                "season_end": [{"source": "card_back_year_and_stats", "value": identity["season_end"]}],
                "parallel_name": [{"source": "visual_texture_plus_checklist", "value": definition.name}],
                "serial_number": [{"source": "front_ocr", "value": identity["serial_number"]}],
                "serial_total": [{"source": "front_ocr", "value": serial_total}],
                "card_number": [{"source": "back_ocr", "value": identity.get("card_number")}],
            },
            "checklist_evidence": [{
                "source_url": CHECKLIST_SOURCE, "source_version": CHECKLIST_VERSION,
                "parallel_name": definition.name, "serial_total": definition.serial_total,
                "channels": list(definition.channels),
            }],
            "market_evidence": visual.get("market_evidence", []) if definition.name == "Gold Diamante" else [],
            "search_queries": candidate_search_queries(identity),
        })
    return sorted(scored, key=lambda item: Decimal(item["confidence"]), reverse=True)


SCOTTIE_BARNES_FIXTURE_VISUAL = {
    "product_season_start": 2025,
    "product_season_end": 2026,
    "serial_number": 27,
    "serial_total": 50,
    "texture_tokens": ["gold", "faceted", "diamante", "cracked ice"],
    "reasons": [
        "front has large gold faceted Diamante pattern",
        "front serial stamp reads 27/50",
        "back is card 31 and contains completed 2024-25 statistics",
        "back copyright is 2025",
    ],
    "market_evidence": [
        {"id": "ebay:scottie-gold-diamante-2025-11-28", "provider_code": "ebay",
         "external_sale_id": "scottie-gold-diamante-2025-11-28", "occurred_at": "2025-11-28T00:00:00Z",
         "sale_price": "17.76", "shipping_price": "0", "currency": "USD",
         "fx_rate_to_base": "6.77", "price_base": "120.24", "evidence_level": "ended_auction",
         "tier": "T0", "reason_codes": ["exact_player_season_product_card_parallel_serial_total_grade"]},
        {"id": "ebay:scottie-gold-diamante-2025-12-26", "provider_code": "ebay",
         "external_sale_id": "scottie-gold-diamante-2025-12-26", "occurred_at": "2025-12-26T00:00:00Z",
         "sale_price": "18.50", "shipping_price": "0", "currency": "USD",
         "fx_rate_to_base": "6.77", "price_base": "125.25", "evidence_level": "ended_auction",
         "tier": "T0", "reason_codes": ["exact_player_season_product_card_parallel_serial_total_grade"]},
    ],
}
