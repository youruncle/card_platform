from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any


ENTITY_TYPES = {
    "manufacturer",
    "brand",
    "license",
    "product",
    "insert",
    "parallel",
    "attribute",
    "player",
    "card",
}
FIELD_ENTITY_TYPES = {
    "brand_name": "brand",
    "product_name": "product",
    "insert_name": "insert",
    "insert": "insert",
    "parallel_name": "parallel",
    "parallel": "parallel",
    "player_name": "player",
}
ATTRIBUTE_FIELDS = {
    "rc": "is_rookie",
    "rookie": "is_rookie",
    "新秀": "is_rookie",
    "正新秀": "is_rookie",
    "auto": "is_autograph",
    "autograph": "is_autograph",
    "签字": "is_autograph",
    "卡签": "is_autograph",
    "patch": "is_patch",
    "relic": "is_patch",
    "物料": "is_patch",
    "球衣": "is_patch",
    "rpa": "rpa",
    "ssp": "is_case_hit",
    "限编": "serial_total",
}


def normalize_knowledge_text(value: object) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", str(value or "").casefold()).strip()


@dataclass(frozen=True, slots=True)
class KnowledgeEntity:
    id: str
    entity_type: str
    canonical_name: str
    aliases: tuple[str, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Resolution:
    canonical_value: str | None
    entity_id: str | None
    matched_alias: str | None
    confidence: float
    version: int | None


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    field: str
    message: str
    severity: str = "error"
    suggested_field: str | None = None
    suggested_value: Any = None


@dataclass(frozen=True, slots=True)
class QueryStage:
    name: str
    query: str
    retained_fields: tuple[str, ...]
    explanation: str


class KnowledgeResolver:
    """Immutable resolver built from one published knowledge snapshot."""

    def __init__(self, snapshot: dict[str, Any] | None = None) -> None:
        snapshot = snapshot or {}
        self.version = snapshot.get("version")
        self.entities: dict[str, KnowledgeEntity] = {}
        self.alias_index: dict[tuple[str, str], KnowledgeEntity] = {}
        self.conflicts: set[frozenset[str]] = set()
        for raw in snapshot.get("entities", []):
            entity = KnowledgeEntity(
                id=str(raw["id"]),
                entity_type=raw["entity_type"],
                canonical_name=raw["canonical_name"],
                aliases=tuple(raw.get("aliases", [])),
                attributes=raw.get("attributes") or {},
            )
            self.entities[entity.id] = entity
            for alias in (entity.canonical_name, *entity.aliases):
                normalized = normalize_knowledge_text(alias)
                if normalized:
                    self.alias_index[(entity.entity_type, normalized)] = entity
        for relation in snapshot.get("relations", []):
            if relation["relation_type"] in {"conflicts_with", "excludes"}:
                self.conflicts.add(
                    frozenset((str(relation["subject_entity_id"]), str(relation["object_entity_id"])))
                )

    def resolve(self, entity_type: str, value: object) -> Resolution:
        normalized = normalize_knowledge_text(value)
        entity = self.alias_index.get((entity_type, normalized))
        if entity is None:
            return Resolution(str(value).strip() or None, None, None, 0.0, self.version)
        return Resolution(entity.canonical_name, entity.id, str(value), 1.0, self.version)

    def find_in_text(self, entity_type: str, value: object) -> list[Resolution]:
        normalized = normalize_knowledge_text(value)
        matches: dict[str, Resolution] = {}
        for (kind, alias), entity in self.alias_index.items():
            if kind == entity_type and alias and alias in normalized:
                matches[entity.id] = Resolution(
                    entity.canonical_name, entity.id, alias, 1.0, self.version
                )
        return sorted(matches.values(), key=lambda item: len(item.matched_alias or ""), reverse=True)

    def aliases_for(self, entity_type: str, value: object) -> tuple[str, ...]:
        resolution = self.resolve(entity_type, value)
        if resolution.entity_id is None:
            return (str(value).strip(),) if str(value).strip() else ()
        entity = self.entities[resolution.entity_id]
        return tuple(dict.fromkeys((entity.canonical_name, *entity.aliases)))

    def validate_identity(self, identity: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        parallel = normalize_knowledge_text(identity.get("parallel_name") or identity.get("parallel"))
        if parallel in {"rc", "rookie", "新秀", "正新秀"}:
            issues.append(
                ValidationIssue(
                    "attribute_in_parallel",
                    "parallel_name",
                    "RC/新秀是领域属性，不能写入 parallel",
                    suggested_field="is_rookie",
                    suggested_value=True,
                )
            )
        card_number = str(identity.get("card_number") or "").strip()
        serial_match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", card_number)
        if serial_match:
            issues.append(
                ValidationIssue(
                    "serial_in_card_number",
                    "card_number",
                    "“编号/总限编”不是 card_number",
                    suggested_field="serial_total",
                    suggested_value=int(serial_match.group(2)),
                )
            )
        elif re.fullmatch(r"/?\s*\d+\s*[编遍]", card_number):
            total = int(re.search(r"\d+", card_number).group())
            issues.append(
                ValidationIssue(
                    "serial_total_in_card_number",
                    "card_number",
                    "限编信息不能写入 card_number",
                    suggested_field="serial_total",
                    suggested_value=total,
                )
            )
        insert_matches = self.find_in_text("insert", identity.get("insert_name") or identity.get("insert"))
        if len({item.entity_id for item in insert_matches}) > 1:
            issues.append(
                ValidationIssue(
                    "mixed_inserts",
                    "insert_name",
                    "字段同时命中多个互斥 insert，请只保留具体卡种",
                )
            )
        selected_ids = {
            result.entity_id
            for field_name, entity_type in FIELD_ENTITY_TYPES.items()
            if (value := identity.get(field_name))
            and (result := self.resolve(entity_type, value)).entity_id
        }
        for pair in self.conflicts:
            if pair <= selected_ids:
                names = [self.entities[entity_id].canonical_name for entity_id in pair]
                issues.append(
                    ValidationIssue(
                        "knowledge_conflict",
                        "identity",
                        f"知识库声明实体冲突：{' / '.join(sorted(names))}",
                    )
                )
        return issues

    def normalize_identity(self, identity: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        normalized = dict(identity)
        corrections: list[dict[str, Any]] = []
        for field_name, entity_type in FIELD_ENTITY_TYPES.items():
            if field_name not in normalized or not normalized[field_name]:
                continue
            result = self.resolve(entity_type, normalized[field_name])
            if result.entity_id and result.canonical_value != normalized[field_name]:
                corrections.append(
                    {
                        "field": field_name,
                        "from": normalized[field_name],
                        "to": result.canonical_value,
                        "entity_id": result.entity_id,
                        "knowledge_version": result.version,
                    }
                )
                normalized[field_name] = result.canonical_value
        return normalized, corrections

    def build_query_plan(self, identity: dict[str, Any], *, max_stages: int = 12) -> list[QueryStage]:
        normalized, _ = self.normalize_identity(identity)
        player = str(normalized.get("player_name") or "").strip()
        player_aliases = self.aliases_for("player", player) or (player,)
        explicit_player_aliases = identity.get("player_name_aliases") or []
        if isinstance(explicit_player_aliases, str):
            explicit_player_aliases = [explicit_player_aliases]
        player_aliases = tuple(
            dict.fromkeys(
                value
                for value in (
                    *player_aliases,
                    *(str(value).strip() for value in explicit_player_aliases),
                )
                if value
            )
        )
        product = str(normalized.get("product_name") or "").strip()
        insert = str(normalized.get("insert_name") or normalized.get("insert") or "").strip()
        parallel = str(normalized.get("parallel_name") or normalized.get("parallel") or "").strip()
        card_number = str(normalized.get("card_number") or "").strip().lstrip("#")
        season_start = normalized.get("season_start")
        season_end = normalized.get("season_end")
        season = (
            f"{season_start}-{str(season_end)[-2:]}"
            if season_start and season_end and season_start != season_end
            else str(season_start or "")
        )
        serial_total = normalized.get("serial_total") or normalized.get("print_run")
        serial = f"{serial_total}编" if serial_total else ""
        attribute = (
            "RPA"
            if normalized.get("is_rookie")
            and normalized.get("is_autograph")
            and normalized.get("is_patch")
            else "AUTO"
            if normalized.get("is_autograph")
            else "RC"
            if normalized.get("is_rookie")
            else ""
        )
        product_aliases = self.aliases_for("product", product) or (product,)
        insert_aliases = self.aliases_for("insert", insert) or (insert,)
        candidates: list[tuple[str, tuple[str, ...], str]] = []
        for product_alias in product_aliases[:3]:
            if insert:
                for insert_alias in insert_aliases[:3]:
                    candidates.extend(
                        [
                            (
                                "exact_insert",
                                (player, product_alias, insert_alias, serial),
                                "球员、产品、insert 与限编优先",
                            ),
                            (
                                "insert_relaxed",
                                (player, insert_alias, serial),
                                "移除产品词，保留身份价值更高的 insert",
                            ),
                        ]
                    )
            candidates.extend(
                [
                    (
                        "exact_product",
                        (player, season, product_alias, card_number, serial),
                        "球员、赛季、产品、卡号与限编",
                    ),
                    (
                        "attribute_product",
                        (player, product_alias, attribute, serial),
                        "使用卖家常用属性词替换不稳定字段",
                    ),
                    (
                        "parallel_product",
                        (player, product_alias, parallel, serial),
                        "保留产品、平行与限编",
                    ),
                    (
                        "product_relaxed",
                        (player, product_alias),
                        "仅保留球员和产品，避免无界宽搜",
                    ),
                ]
            )
        for player_alias in player_aliases[1:]:
            for product_alias in product_aliases[:2]:
                candidates.append(
                    (
                        "player_product_alias",
                        (player_alias, product_alias),
                        "球员卖家别名与产品宽搜，避免译名或标题省略字段导致漏召回",
                    )
                )
        candidates.append(
            ("serial_fallback", (player, season, serial), "产品别名未命中时保留赛季与限编")
        )
        if attribute or serial:
            candidates.append(
                ("bounded_fallback", (player, attribute, serial), "最后的有界属性回退")
            )
        if len(player_aliases) > 1:
            alias_candidates = [row for row in candidates if row[0] == "player_product_alias"]
            reserved_alias_candidates: list[tuple[str, tuple[str, ...], str]] = []
            reserved_players: set[str] = set()
            for row in alias_candidates:
                alias_player = row[1][0]
                if alias_player in reserved_players:
                    continue
                reserved_players.add(alias_player)
                reserved_alias_candidates.append(row)
                if len(reserved_alias_candidates) >= 2:
                    break
            regular_candidates = [row for row in candidates if row[0] != "player_product_alias"]
            candidates = [
                *regular_candidates[: max(0, max_stages - len(reserved_alias_candidates))],
                *reserved_alias_candidates,
                *regular_candidates[max(0, max_stages - len(reserved_alias_candidates)) :],
            ]
        stages: list[QueryStage] = []
        seen: set[str] = set()
        for name, parts, explanation in candidates:
            query = " ".join(filter(None, (str(part).strip() for part in parts)))
            if not query or query in seen:
                continue
            seen.add(query)
            stages.append(
                QueryStage(
                    name,
                    query,
                    tuple(
                        field_name
                        for field_name, value in (
                            ("player", player),
                            ("season", season),
                            ("product", product),
                            ("card_number", card_number),
                            ("insert", insert),
                            ("parallel", parallel),
                            ("serial_total", serial),
                            ("attribute", attribute),
                        )
                        if value and value in query
                    ),
                    explanation,
                )
            )
            if len(stages) >= max_stages:
                break
        return stages


def snapshot_checksum(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
