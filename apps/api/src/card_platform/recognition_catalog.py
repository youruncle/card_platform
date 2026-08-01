import re
import unicodedata
from decimal import Decimal
from typing import Any

from card_platform.schemas import CardRecognitionResult

FIELD_WEIGHTS = {
    "player_name": Decimal("0.28"),
    "season_start": Decimal("0.08"),
    "season_end": Decimal("0.04"),
    "product_name": Decimal("0.16"),
    "insert_name": Decimal("0.13"),
    "parallel_name": Decimal("0.11"),
    "card_number": Decimal("0.14"),
    "serial_total": Decimal("0.04"),
    "is_rookie": Decimal("0.01"),
    "is_autograph": Decimal("0.005"),
    "is_patch": Decimal("0.005"),
}
TEXT_FIELDS = {
    "player_name",
    "product_name",
    "insert_name",
    "parallel_name",
    "card_number",
}
BOOLEAN_FIELDS = {"is_rookie", "is_autograph", "is_patch"}
COMPONENT_TYPES = {"insert": "insert_name", "parallel": "parallel_name"}


def machine_training_card_entity(
    sample_id: object,
    identity: dict[str, Any],
    *,
    confidence: object,
) -> dict[str, Any]:
    """Adapt a source-assisted training truth to the read-only catalog shape."""
    parts = [
        identity.get("player_name"),
        (
            f"{identity.get('season_start')}-{str(identity.get('season_end'))[-2:]}"
            if identity.get("season_start") and identity.get("season_end")
            else identity.get("season_start")
        ),
        identity.get("product_name"),
        identity.get("insert_name"),
        identity.get("parallel_name"),
        f"#{identity.get('card_number')}" if identity.get("card_number") else None,
        f"/{identity.get('serial_total')}" if identity.get("serial_total") else None,
    ]
    return {
        "id": f"machine-training:{sample_id}",
        "entity_type": "card",
        "canonical_name": " · ".join(str(part) for part in parts if part),
        "aliases": [],
        "attributes": {
            "player": identity.get("player_name"),
            "season_start": identity.get("season_start"),
            "season_end": identity.get("season_end"),
            "brand": identity.get("brand_name"),
            "product": identity.get("product_name"),
            "insert": identity.get("insert_name"),
            "parallel": identity.get("parallel_name"),
            "card_number": identity.get("card_number"),
            "serial_total": identity.get("serial_total"),
            "is_rookie": identity.get("is_rookie"),
            "rookie_evidence": identity.get("rookie_evidence") or [],
            "is_autograph": identity.get("is_autograph"),
            "autograph_type": identity.get("autograph_type"),
            "has_inscription": identity.get("has_inscription"),
            "inscription_text": identity.get("inscription_text"),
            "is_memorabilia": identity.get("is_memorabilia"),
            "memorabilia_types": identity.get("memorabilia_types") or [],
            "memorabilia_usage": identity.get("memorabilia_usage"),
            "memorabilia_player": identity.get("memorabilia_player"),
            "is_patch": identity.get("is_patch"),
            "machine_training_confidence": str(confidence),
            "machine_training": True,
        },
    }


def normalize_catalog_text(value: Any) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    ascii_like = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", ascii_like)


def _text_matches(first: Any, second: Any) -> bool:
    left = normalize_catalog_text(first)
    right = normalize_catalog_text(second)
    if not left or not right:
        return False
    return left == right or (len(left) >= 5 and len(right) >= 5 and (left in right or right in left))


def _scope_matches(visual: CardRecognitionResult, attributes: dict[str, Any]) -> bool:
    season_start = attributes.get("season_start")
    if (
        visual.season_start is not None
        and season_start is not None
        and visual.season_start != season_start
    ):
        return False
    product = attributes.get("product")
    products = attributes.get("products") or []
    if product:
        products = [product, *products]
    return not (
        visual.product_name
        and products
        and not any(_text_matches(visual.product_name, item) for item in products)
    )


def _component_labels(entity: dict[str, Any]) -> list[str]:
    attributes = entity.get("attributes") or {}
    labels = [
        *(entity.get("aliases") or []),
        attributes.get("color"),
        attributes.get("pattern"),
        attributes.get("finish"),
    ]
    if attributes.get("color") and attributes.get("pattern"):
        labels.append(f"{attributes['color']} {attributes['pattern']}")
    canonical_name = entity.get("canonical_name")
    if canonical_name:
        labels.append(canonical_name)
    return list(dict.fromkeys(str(label).strip() for label in labels if label))


def _preferred_component_name(entity: dict[str, Any], observed: Any) -> str:
    labels = _component_labels(entity)
    attributes = entity.get("attributes") or {}
    if (
        attributes.get("color")
        and attributes.get("pattern")
        and observed
        and _text_matches(observed, attributes.get("color"))
    ):
        return f"{attributes['color']} {attributes['pattern']}"
    for label in labels:
        if observed and _text_matches(observed, label):
            return label
    if attributes.get("color") and attributes.get("pattern"):
        return f"{attributes['color']} {attributes['pattern']}"
    for key in ("color", "pattern", "finish"):
        if attributes.get(key):
            return str(attributes[key])
    aliases = entity.get("aliases") or []
    return str(aliases[0] if aliases else entity.get("canonical_name") or "").strip()


def parallel_option_name(entity: dict[str, Any]) -> str:
    """Derive the concise checklist parallel name from a scoped entity."""
    attributes = entity.get("attributes") or {}
    if attributes.get("parallel_name"):
        return str(attributes["parallel_name"]).strip()
    if attributes.get("color") and attributes.get("pattern"):
        return f"{attributes['color']} {attributes['pattern']}"
    for key in ("pattern", "color"):
        if attributes.get(key):
            value = str(attributes[key]).strip()
            finish = str(attributes.get("finish") or "").strip()
            return f"{value} {finish}".strip()
    aliases = entity.get("aliases") or []
    if aliases:
        return str(aliases[0]).strip()
    canonical = str(entity.get("canonical_name") or "")
    removable = " ".join(
        str(attributes.get(key) or "")
        for key in ("product", "insert", "parent_insert", "parent_set")
    )
    removable_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", removable.casefold())
        if token not in {"base"}
    }
    removable_tokens.update(
        token[:-1]
        for token in tuple(removable_tokens)
        if token.endswith("s") and len(token) > 4
    )
    kept = [
        token
        for token in re.findall(r"[A-Za-z0-9]+", canonical)
        if token.casefold() not in removable_tokens
        and not re.fullmatch(r"(?:19|20)\d{2}", token)
        and not re.fullmatch(r"\d{2}", token)
        and token.casefold() not in {"panini", "topps", "basketball"}
    ]
    return " ".join(kept).strip()


def scoped_parallel_options(
    visual: CardRecognitionResult,
    entities: list[dict[str, Any]],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return legal parallel choices scoped by release, set and print run."""
    options: list[tuple[Decimal, dict[str, Any]]] = []
    for entity in entities:
        entity_type = entity.get("entity_type")
        attributes = entity.get("attributes") or {}
        if entity_type not in {"parallel", "card"} or (
            entity_type == "card" and not attributes.get("parallel_name")
        ):
            continue
        if not _scope_matches(visual, attributes):
            continue
        serial_total = attributes.get("serial_total")
        if (
            visual.serial_total is not None
            and serial_total is not None
            and visual.serial_total != serial_total
        ):
            continue
        scoped_insert = (
            attributes.get("insert")
            or attributes.get("parent_insert")
            or attributes.get("parent_set")
        )
        insert_conflict = bool(
            visual.insert_name
            and scoped_insert
            and not _text_matches(visual.insert_name, scoped_insert)
        )
        serial_match = bool(
            visual.serial_total is not None
            and serial_total is not None
            and visual.serial_total == serial_total
        )
        if insert_conflict and not serial_match:
            continue
        name = parallel_option_name(entity)
        if not name:
            continue
        score = Decimal("0.2")
        matching_fields = ["product_name"]
        if attributes.get("season_start") == visual.season_start:
            score += Decimal("0.15")
            matching_fields.append("season_start")
        if serial_match:
            score += Decimal("0.4")
            matching_fields.append("serial_total")
        if visual.parallel_name and any(
            _text_matches(visual.parallel_name, label)
            for label in [name, *_component_labels(entity)]
        ):
            score += Decimal("0.2")
            matching_fields.append("parallel_name")
        if visual.insert_name and scoped_insert and _text_matches(
            visual.insert_name, scoped_insert
        ):
            score += Decimal("0.1")
            matching_fields.append("insert_name")
        elif insert_conflict:
            score -= Decimal("0.08")
        options.append(
            (
                score,
                {
                    "name": name,
                    "canonical_name": entity.get("canonical_name"),
                    "serial_total": serial_total,
                    "matching_fields": matching_fields,
                    "conflicts": ["insert_name"] if insert_conflict else [],
                    "knowledge_entity_id": str(entity.get("id")),
                    "score": str(min(score, Decimal("1"))),
                },
            )
        )
    options.sort(key=lambda item: item[0], reverse=True)
    distinct: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, option in options:
        token = normalize_catalog_text(option["name"])
        if not token or token in seen:
            continue
        seen.add(token)
        distinct.append(option)
        if len(distinct) == limit:
            break
    return distinct


def rank_compositional_candidates(
    visual: CardRecognitionResult,
    entities: list[dict[str, Any]],
    *,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Build auditable component variants without changing the visual Top-1.

    A component must be scoped to the observed product/season and have either a
    textual component match or an exact serial-total anchor. This deliberately
    avoids free-form combinations across products.
    """
    base = visual.model_dump(mode="json")
    ranked: list[tuple[Decimal, dict[str, Any]]] = []
    for entity in entities:
        entity_type = entity.get("entity_type")
        field = COMPONENT_TYPES.get(str(entity_type))
        if not field:
            continue
        attributes = entity.get("attributes") or {}
        if not _scope_matches(visual, attributes):
            continue
        observed = base.get(field)
        labels = _component_labels(entity)
        text_match = bool(
            observed and any(_text_matches(observed, label) for label in labels)
        )
        serial_total = attributes.get("serial_total")
        serial_match = bool(
            entity_type == "parallel"
            and visual.serial_total is not None
            and serial_total is not None
            and visual.serial_total == serial_total
        )
        if not text_match and not serial_match:
            continue
        component_name = _preferred_component_name(entity, observed)
        if not component_name or normalize_catalog_text(component_name) == normalize_catalog_text(observed):
            continue
        identity = dict(base)
        identity[field] = component_name
        matching_fields = ["product_name"]
        score = Decimal("0.34")
        if visual.season_start is not None and attributes.get("season_start") == visual.season_start:
            matching_fields.append("season_start")
            score += Decimal("0.12")
        if text_match:
            matching_fields.append(field)
            score += Decimal("0.25")
        if serial_match:
            matching_fields.append("serial_total")
            score += Decimal("0.24")
        ranked.append(
            (
                min(score, Decimal("0.95")),
                {
                    "title": f"组件知识组合：{entity.get('canonical_name') or component_name}",
                    "identity": identity,
                    "matching_fields": matching_fields,
                    "conflicts": [],
                    "match_confidence": str(min(score, Decimal("0.95"))),
                    "knowledge_entity_id": str(entity.get("id")),
                    "source_type": "knowledge_composition",
                },
            )
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    distinct: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for _, candidate in ranked:
        identity = candidate["identity"]
        key = (
            normalize_catalog_text(identity.get("insert_name")),
            normalize_catalog_text(identity.get("parallel_name")),
        )
        if key in seen:
            continue
        seen.add(key)
        distinct.append(candidate)
        if len(distinct) == limit:
            break
    return distinct


def rank_structured_uncertainty_candidates(
    visual: CardRecognitionResult,
    *,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Expose common single-field ambiguities as explicit Top-3 alternatives."""
    base = visual.model_dump(mode="json")
    variants: list[tuple[str, dict[str, Any], Decimal]] = []
    copyright_year = visual.copyright_year
    season_evidence = " ".join(
        [
            *(visual.field_evidence.get("season_start") or []),
            *(visual.field_evidence.get("season_end") or []),
        ]
    )
    statistical_season = bool(
        re.search(
            r"\b(?:record|school|college|stats?|career|draft(?:ed)?|from)\b|"
            r"统计|履历|生涯|战绩|校队|大学|选秀",
            season_evidence,
            re.IGNORECASE,
        )
    )
    if copyright_year is not None and (
        visual.season_start is None or statistical_season
    ):
        # Copyright is a release anchor, but manufacturers use both the start
        # and end year. Expose both bounded hypotheses instead of converting a
        # college/career year into a confident release season.
        start_year_distance = (
            abs(copyright_year - visual.season_start)
            if visual.season_start is not None
            else 1
        )
        end_year_distance = (
            abs((copyright_year - 1) - visual.season_start)
            if visual.season_start is not None
            else 1
        )
        variants.extend(
            [
                (
                    "版权年候选：版权年作为赛季起始年",
                    {
                        "season_start": copyright_year,
                        "season_end": copyright_year + 1,
                    },
                    Decimal("0.76") - Decimal("0.03") * start_year_distance,
                ),
                (
                    "版权年候选：版权年作为赛季结束年",
                    {
                        "season_start": copyright_year - 1,
                        "season_end": copyright_year,
                    },
                    Decimal("0.75") - Decimal("0.03") * end_year_distance,
                ),
            ]
        )
    if visual.season_start is not None and visual.season_end is not None:
        variants.extend(
            [
                (
                    "版权年/发行年歧义：赛季整体后移一年",
                    {
                        "season_start": visual.season_start + 1,
                        "season_end": visual.season_end + 1,
                    },
                    Decimal("0.68"),
                ),
                (
                    "版权年/发行年歧义：赛季整体前移一年",
                    {
                        "season_start": visual.season_start - 1,
                        "season_end": visual.season_end - 1,
                    },
                    Decimal("0.60"),
                ),
            ]
        )
    if visual.is_rookie is None:
        variants.append(("RC 状态未知，候选按非 RC", {"is_rookie": False}, Decimal("0.62")))
    elif visual.is_rookie:
        variants.append(("RC 标识可能属于新秀主题而非 RC", {"is_rookie": False}, Decimal("0.58")))
    if visual.is_numbered:
        score = Decimal("0.61") if visual.serial_total is None else Decimal("0.54")
        variants.append(("限编标识存在版式歧义", {"is_numbered": False}, score))
    if visual.is_autograph and visual.autograph_type in {"on_card", "sticker"}:
        alternate = "sticker" if visual.autograph_type == "on_card" else "on_card"
        variants.append(("签字承载方式存在视觉歧义", {"autograph_type": alternate}, Decimal("0.56")))
    variants.sort(key=lambda item: item[2], reverse=True)
    distinct_variants: list[tuple[str, dict[str, Any], Decimal]] = []
    seen_updates: set[tuple[tuple[str, Any], ...]] = set()
    for title, updates, score in variants:
        if not any(
            getattr(visual, field, None) != value for field, value in updates.items()
        ):
            continue
        key = tuple(sorted(updates.items()))
        if key in seen_updates:
            continue
        seen_updates.add(key)
        distinct_variants.append((title, updates, score))
    return [
        {
            "title": title,
            "identity": {**base, **updates},
            "matching_fields": [],
            "conflicts": list(updates),
            "match_confidence": str(score),
            "knowledge_entity_id": None,
            "source_type": "structured_uncertainty",
        }
        for title, updates, score in distinct_variants[:limit]
    ]


def knowledge_card_identity(entity: dict[str, Any]) -> dict[str, Any]:
    attributes = entity.get("attributes") or {}
    def first(*keys: str) -> Any:
        for key in keys:
            if key in attributes:
                return attributes[key]
        return None

    return {
        "player_name": first("player", "player_name"),
        "season_start": first("season_start"),
        "season_end": first("season_end"),
        "brand_name": first("brand", "brand_name"),
        "product_name": first("product", "product_name"),
        "insert_name": first("insert", "insert_name"),
        "parallel_name": first("parallel", "parallel_name"),
        "card_number": first("card_number"),
        "serial_number": None,
        "serial_total": first("serial_total", "print_run"),
        "grade_company": None,
        "grade_value": None,
        "is_rookie": first("is_rookie", "is_rookie_card"),
        "rookie_evidence": first("rookie_evidence") or [],
        "is_autograph": first("is_autograph"),
        "autograph_type": first("autograph_type"),
        "has_inscription": first("has_inscription"),
        "inscription_text": first("inscription_text"),
        "is_memorabilia": first("is_memorabilia"),
        "memorabilia_types": first("memorabilia_types") or [],
        "memorabilia_usage": first("memorabilia_usage"),
        "memorabilia_player": first("memorabilia_player"),
        "is_patch": first("is_patch"),
    }


def rank_knowledge_cards(
    visual: CardRecognitionResult,
    entities: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    visual_identity = visual.model_dump(mode="python")
    ranked: list[dict[str, Any]] = []
    for entity in entities:
        if entity.get("entity_type") != "card":
            continue
        identity = knowledge_card_identity(entity)
        if visual.player_name and identity["player_name"] and not _text_matches(
            visual.player_name, identity["player_name"]
        ):
            continue
        score = Decimal("0")
        matching_fields: list[str] = []
        conflicts: list[str] = []
        anchor_matches = 0
        for field, weight in FIELD_WEIGHTS.items():
            observed = visual_identity.get(field)
            expected = identity.get(field)
            if observed in (None, "") or expected in (None, ""):
                continue
            if field in TEXT_FIELDS:
                matched = _text_matches(observed, expected)
            elif field in BOOLEAN_FIELDS:
                matched = bool(observed) == bool(expected)
            else:
                matched = observed == expected
            if matched:
                score += weight
                matching_fields.append(field)
                if field in {
                    "season_start",
                    "product_name",
                    "insert_name",
                    "parallel_name",
                    "card_number",
                    "serial_total",
                }:
                    anchor_matches += 1
            elif field not in BOOLEAN_FIELDS or bool(observed):
                conflicts.append(field)
                score -= weight
        if "player_name" not in matching_fields or anchor_matches < 2:
            continue
        score = max(Decimal("0"), min(Decimal("1"), score))
        ranked.append(
            {
                "title": entity.get("canonical_name") or "知识库具体卡片",
                "identity": identity,
                "matching_fields": matching_fields,
                "conflicts": conflicts,
                "match_confidence": str(score),
                "knowledge_entity_id": str(entity.get("id")),
            }
        )
    ranked.sort(
        key=lambda item: (
            Decimal(item["match_confidence"]),
            len(item["matching_fields"]),
            -len(item["conflicts"]),
        ),
        reverse=True,
    )
    return ranked[:limit]
