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
MOSAIC_2020_CHECKLIST_VERSION = "beckett-2020-21-panini-mosaic-2026-08-13"
MOSAIC_2020_CHECKLIST_SOURCE = (
    "https://www.beckett.com/news/2020-21-panini-mosaic-basketball-cards/"
)
CROWN_ROYALE_2023_CHECKLIST_VERSION = "beckett-2023-24-crown-royale-2026-08-13"
CROWN_ROYALE_2023_CHECKLIST_SOURCE = (
    "https://www.beckett.com/news/2023-24-panini-crown-royale-basketball-cards/"
)


def candidate_search_queries(identity: dict[str, Any]) -> list[str]:
    season = f"{identity['season_start']}-{str(identity.get('season_end') or '')[-2:]}"
    descriptors = " ".join(
        str(value) for value in (
            identity.get("insert_name"), identity.get("parallel_name"),
        ) if value
    ) or "Base"
    serial = f"/{identity['serial_total']}" if identity.get("serial_total") else ""
    return [
        f'"{identity["player_name"]}" "{identity["card_number"]}" "{descriptors}" {serial} {season} sold'.strip(),
        f'"{identity["player_name"]}" "{descriptors}" completed sale',
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
    product = str(visual.get("product_name") or current.get("product_name") or "").casefold()
    brand = str(current.get("brand_name") or "").casefold()
    season_start = visual.get("product_season_start", current.get("season_start"))
    preserve_insert = False
    if visual.get("identity_overrides"):
        identity = {**current, **visual["identity_overrides"]}
        valid, reasons = validate_candidate(identity)
        return [{
            "identity": identity,
            "confidence": str(visual.get("confidence") or "0.90"),
            "is_valid": valid,
            "validation_reasons": reasons,
            "visual_reasons": visual.get("reasons", []),
            "field_provenance": visual.get("field_provenance", {}),
            "checklist_evidence": visual.get("checklist_evidence", []),
            "market_evidence": visual.get("market_evidence", []),
            "search_queries": candidate_search_queries(identity),
        }]
    if (
        "crown royale" in product
        and season_start == 2023
        and str(current.get("insert_name") or "").casefold() == "test of time"
    ):
        definitions = [
            ParallelDefinition("Blue", 75, ("blue", "cyan", "aqua", "circle foil"), ("hobby",))
        ] if serial_total == 75 else []
        checklist_source = CROWN_ROYALE_2023_CHECKLIST_SOURCE
        checklist_version = CROWN_ROYALE_2023_CHECKLIST_VERSION
        preserve_insert = True
    elif "mosaic" in product and season_start == 2020:
        definitions = [
            ParallelDefinition(
                "Fast Break Purple Mosaic",
                50,
                ("purple", "fast break", "disco", "circles"),
                ("fast break",),
            )
        ] if serial_total == 50 else []
        checklist_source = MOSAIC_2020_CHECKLIST_SOURCE
        checklist_version = MOSAIC_2020_CHECKLIST_VERSION
    elif "topps" in brand or "flagship" in product:
        definitions = [
            item for item in TOPPS_2025_FLAGSHIP_PARALLELS
            if item.serial_total == serial_total
        ]
        checklist_source = CHECKLIST_SOURCE
        checklist_version = CHECKLIST_VERSION
    else:
        definitions = []

    # An unnumbered Prizm card with no visible parallel identifiers is a valid
    # Base candidate. "Prizm" is the product technology, not an insert or a
    # confirmed parallel name.
    if "prizm" in product and serial_total is None and visual.get("base_card") is True:
        identity = {
            **current,
            "insert_name": None,
            "parallel_name": None,
            "set_name": current.get("set_name") or "Base",
            "serial_number": None,
            "serial_total": None,
        }
        valid, reasons = validate_candidate(identity)
        return [{
            "identity": identity,
            "confidence": str(visual.get("confidence") or "0.90"),
            "is_valid": valid,
            "validation_reasons": reasons,
            "visual_reasons": visual.get("reasons", []),
            "field_provenance": {
                "insert_name": [{"source": "front_visual_plus_checklist", "value": None}],
                "parallel_name": [{"source": "front_visual_plus_checklist", "value": None}],
                "card_number": [{"source": "back_ocr", "value": identity.get("card_number")}],
            },
            "checklist_evidence": visual.get("checklist_evidence", []),
            "market_evidence": visual.get("market_evidence", []),
            "search_queries": candidate_search_queries({**identity, "parallel_name": "Base"}),
        }]
    scored = []
    for definition in definitions:
        overlap = sum(any(token in observed or observed in token for observed in tokens)
                      for token in definition.visual_tokens)
        hint = str(visual.get("parallel_name_hint") or "").casefold()
        hint_match = bool(hint and definition.name.casefold() == hint)
        confidence = min(
            Decimal("0.96"),
            Decimal("0.58") + Decimal(overlap) * Decimal("0.11")
            + (Decimal("0.12") if hint_match else Decimal("0")),
        )
        observation_confidence = Decimal(str(visual.get("confidence") or "0"))
        if len(definitions) == 1 and observation_confidence >= Decimal("0.65"):
            # Product + season + serial total can uniquely identify a checklist
            # parallel even when low-resolution texture words are incomplete.
            confidence = max(
                confidence,
                min(Decimal("0.90"), observation_confidence * Decimal("0.92")),
            )
        identity = {
            **current,
            "season_start": season_start,
            "season_end": visual.get("product_season_end", current.get("season_end")),
            "product_name": visual.get("product_name", current.get("product_name")),
            "card_number": visual.get("card_number") or current.get("card_number"),
            "parallel_name": definition.name,
            "serial_number": visual.get("serial_number", current.get("serial_number")),
            "serial_total": serial_total,
            "set_name": current.get("set_name") or (
                current.get("insert_name") if preserve_insert else "Base"
            ),
            "insert_name": current.get("insert_name") if preserve_insert else None,
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
                "source_url": checklist_source, "source_version": checklist_version,
                "parallel_name": definition.name, "serial_total": definition.serial_total,
                "channels": list(definition.channels),
            }],
            "market_evidence": visual.get("market_evidence", [])
            if definition.name in {"Gold Diamante", "Blue"} else [],
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


BRANDON_MILLER_FIXTURE_VISUAL = {
    "base_card": True,
    "confidence": "0.94",
    "texture_tokens": ["silver border", "black base background", "no color parallel"],
    "reasons": [
        "front design matches the 2023-24 Panini Prizm base rookie #152",
        "front has no numbered stamp or distinct named parallel treatment",
        "Prizm is the product mark and must not be stored as insert or parallel",
    ],
    "checklist_evidence": [{
        "source_url": "https://www.comc.com/Cards/Basketball/2023-24/Panini_Prizm_-_Base/1521/Brandon_Miller/24825317",
        "source_version": "comc-2023-24-prizm-base-2026-08-13",
        "parallel_name": None,
        "serial_total": None,
    }],
    "market_evidence": [
        {
            "id": "sports-card-investor:brandon-prizm-base-2026-07-08",
            "provider_code": "sports_card_investor",
            "external_sale_id": "brandon-prizm-base-2026-07-08-1.25",
            "occurred_at": "2026-07-08T00:00:00Z",
            "sale_price": "1.25",
            "shipping_price": "0",
            "currency": "USD",
            "fx_rate_to_base": "6.77",
            "price_base": "8.46",
            "evidence_level": "aggregated_sale",
            "tier": "T0",
            "source_url": (
                "https://www.sportscardsinvestor.com/cards/"
                "brandon-miller-basketball/2023-prizm-base-152"
            ),
            "reason_codes": [
                "exact_player_season_product_card_base_raw",
                "sale_date_and_price_reported_by_price_aggregator",
            ],
        },
        {
            "id": "sports-card-investor:brandon-prizm-base-2026-07-06",
            "provider_code": "sports_card_investor",
            "external_sale_id": "brandon-prizm-base-2026-07-06-0.99",
            "occurred_at": "2026-07-06T00:00:00Z",
            "sale_price": "0.99",
            "shipping_price": "0",
            "currency": "USD",
            "fx_rate_to_base": "6.77",
            "price_base": "6.70",
            "evidence_level": "aggregated_sale",
            "tier": "T0",
            "source_url": (
                "https://www.sportscardsinvestor.com/cards/"
                "brandon-miller-basketball/2023-prizm-base-152"
            ),
            "reason_codes": [
                "exact_player_season_product_card_base_raw",
                "sale_date_and_price_reported_by_price_aggregator",
            ],
        },
    ],
}


JAREN_JACKSON_FIXTURE_VISUAL = {
    "confidence": "0.95",
    "product_name": "Mosaic Basketball",
    "product_season_start": 2020,
    "product_season_end": 2021,
    "serial_number": 22,
    "serial_total": 50,
    "texture_tokens": ["purple", "blue", "fast break", "disco", "circles"],
    "reasons": [
        "back copyright and season line identify 2020-21 Mosaic Basketball",
        "back card number reads No. 29 and serial stamp reads 22/50",
        "front has the circular Fast Break/Disco pattern with purple treatment",
        "official checklist maps the /50 base parallel to Fast Break Purple Mosaic",
    ],
}


JAREN_JACKSON_88_FIXTURE_VISUAL = {
    "confidence": "0.97",
    "identity_overrides": {
        "season_start": 2020, "season_end": 2021,
        "product_name": "Mosaic Basketball", "set_name": "Base",
        "card_number": "29", "insert_name": None,
        "parallel_name": "Fusion Red and Yellow Choice Mosaic",
        "serial_number": 73, "serial_total": 88,
    },
    "reasons": [
        "back reads No. 29 and 2020-21 Panini Mosaic Basketball",
        "front serial stamp reads 73/88",
        "red/yellow Choice Mosaic treatment matches the checklist /88 parallel",
    ],
    "field_provenance": {
        "season_start": [{"source": "card_back_ocr", "value": 2020}],
        "card_number": [{"source": "card_back_ocr", "value": "29"}],
        "parallel_name": [{"source": "front_visual_plus_checklist", "value": "Fusion Red and Yellow Choice Mosaic"}],
        "serial_number": [{"source": "front_ocr", "value": 73}],
        "serial_total": [{"source": "front_ocr", "value": 88}],
    },
    "checklist_evidence": [{"source_url": MOSAIC_2020_CHECKLIST_SOURCE,
                            "source_version": MOSAIC_2020_CHECKLIST_VERSION,
                            "parallel_name": "Fusion Red and Yellow Choice Mosaic",
                            "serial_total": 88}],
}

ANDREW_NEMBHARD_FIXTURE_VISUAL = {
    "confidence": "0.96",
    "identity_overrides": {
        "season_start": 2023, "season_end": 2024,
        "product_name": "Donruss Elite Basketball", "set_name": "Base",
        "card_number": "83", "insert_name": None, "parallel_name": "Aspirations",
        "serial_number": 5, "serial_total": 8,
    },
    "reasons": [
        "back reads No. 83 and 2023-24 Panini Donruss Elite Basketball",
        "front serial stamp reads 5/8",
        "circular fireworks foil matches the base Aspirations parallel",
    ],
    "field_provenance": {
        "season_start": [{"source": "card_back_ocr", "value": 2023}],
        "card_number": [{"source": "card_back_ocr", "value": "83"}],
        "parallel_name": [{"source": "front_visual_plus_checklist", "value": "Aspirations"}],
        "serial_number": [{"source": "front_ocr", "value": 5}],
        "serial_total": [{"source": "front_ocr", "value": 8}],
    },
    "checklist_evidence": [{"source_url": "https://www.beckett.com/news/2023-24-donruss-elite-basketball-cards/",
                            "source_version": "beckett-2023-24-donruss-elite-2026-08-15",
                            "parallel_name": "Aspirations", "serial_total": 8}],
}

STEPHEN_CURRY_FIXTURE_VISUAL = {
    "confidence": "0.96",
    "identity_overrides": {
        "season_start": 2025, "season_end": 2026, "brand_name": "Topps",
        "product_name": "Topps Basketball", "set_name": "Stars of the NBA",
        "card_number": "SA-5", "insert_name": "Stars of the NBA",
        "parallel_name": None, "serial_number": None, "serial_total": None,
    },
    "reasons": [
        "front design reads Stars and matches the Stars of the NBA insert",
        "back card code reads SA-5 and copyright reads 2025",
        "no serial stamp or named parallel treatment is visible",
    ],
    "field_provenance": {
        "card_number": [{"source": "card_back_ocr", "value": "SA-5"}],
        "insert_name": [{"source": "front_visual_plus_checklist", "value": "Stars of the NBA"}],
        "parallel_name": [{"source": "no_parallel_marker_visible", "value": None}],
    },
    "checklist_evidence": [{"source_url": CHECKLIST_SOURCE,
                            "source_version": CHECKLIST_VERSION,
                            "card_number": "SA-5", "insert_name": "Stars of the NBA"}],
}

LEBRON_JAMES_FIXTURE_VISUAL = {
    "confidence": "0.96",
    "identity_overrides": {
        "season_start": 2025, "season_end": 2026, "brand_name": "Topps",
        "product_name": "NBA Hoops Basketball", "set_name": "Bounce House",
        "card_number": "BH-2", "insert_name": "Bounce House",
        "parallel_name": None, "serial_number": None, "serial_total": None,
    },
    "reasons": [
        "front identifies the NBA Hoops Bounce House insert",
        "back card code reads BH-2 and copyright reads 2026",
        "no serial stamp or checklist-specific parallel treatment is visible",
    ],
    "field_provenance": {
        "card_number": [{"source": "card_back_ocr", "value": "BH-2"}],
        "insert_name": [{"source": "front_visual_plus_checklist", "value": "Bounce House"}],
        "parallel_name": [{"source": "no_parallel_marker_visible", "value": None}],
    },
    "checklist_evidence": [{"source_url": "https://www.beckett.com/news/2025-26-topps-nba-hoops-basketball-cards/",
                            "source_version": "beckett-2025-26-topps-nba-hoops-2026-08-15",
                            "card_number": "BH-2", "insert_name": "Bounce House"}],
}

ACE_BAILEY_FIXTURE_VISUAL = {
    "confidence": "0.88",
    "identity_overrides": {
        "season_start": 2025, "season_end": 2026, "brand_name": "Topps",
        "product_name": "Topps Chrome Basketball", "set_name": "Base",
        "card_number": "255", "insert_name": None,
        "parallel_name": "Basketball Refractor", "serial_number": None,
        "serial_total": None, "is_rookie": True,
    },
    "reasons": [
        "back identifies card 255 and references the 2025 NBA draft",
        "front has the basketball-texture Refractor treatment",
        "no serial stamp is visible, so the numbered Orange Basketball /25 variant is excluded",
    ],
    "field_provenance": {
        "season_start": [{"source": "card_back_stats_and_draft_year", "value": 2025}],
        "card_number": [{"source": "card_back_ocr", "value": "255"}],
        "parallel_name": [{"source": "front_texture_plus_absence_of_serial_stamp", "value": "Basketball Refractor"}],
    },
    "checklist_evidence": [{"source_url": "https://www.beckett.com/news/2025-26-topps-chrome-basketball-cards/",
                            "source_version": "beckett-2025-26-topps-chrome-2026-08-15",
                            "card_number": "255", "parallel_name": "Basketball Refractor"}],
}


LUKA_DONCIC_FIXTURE_VISUAL = {
    "confidence": "0.96",
    "product_name": "Crown Royale Basketball",
    "product_season_start": 2023,
    "product_season_end": 2024,
    "card_number": "4",
    "serial_number": 10,
    "serial_total": 75,
    "parallel_name_hint": "Blue",
    "texture_tokens": ["blue", "cyan", "aqua", "circle foil"],
    "reasons": [
        "front title identifies the Test of Time insert and the player as Luka Doncic",
        "front serial stamp reads 10/75",
        "front treatment is blue/cyan; it conflicts with the stored Fusion Red and Yellow name",
        "the 2023-24 Crown Royale checklist maps Test of Time /75 to the Blue parallel",
        "the checklist identifies Luka Doncic as Test of Time card 4",
    ],
}
