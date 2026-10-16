from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from card_platform.knowledge import normalize_knowledge_text


REQUIRED_REGRESSIONS = {
    "First Steps",
    "Dreamland",
    "Night Owls",
    "National Treasures",
    "One and One",
    "RPA",
    "Donruss",
    "Donruss Optic",
}
ALLOWED_COVERAGE = {"covered", "partial", "needs_review", "pending_source", "not_applicable"}
ALLOWED_APPLICABILITY = {"verified", "partial", "candidate", "discontinued"}
ALLOWED_SOURCE_TYPES = {
    "official_product",
    "official_checklist",
    "official_odds",
    "beckett_checklist",
    "cardboard_connection",
    "other",
}
ALLOWED_ENTITY_TYPES = {
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
ALLOWED_RELATION_TYPES = {
    "conflicts_with",
    "excludes",
    "requires",
    "belongs_to",
    "equivalent_to",
    "manufactured_by",
    "licensed_by",
    "product_family_of",
    "available_in",
}
ALLOWED_ALIAS_TYPES = {"canonical", "synonym", "translation", "misspelling", "seller_term"}


@dataclass(frozen=True, slots=True)
class ManifestReport:
    checksum: str
    summary: dict[str, int]
    conflicts: tuple[dict[str, Any], ...]
    replay: dict[str, bool]
    publishable: bool


def load_manifest(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def manifest_checksum(manifest: dict[str, Any]) -> str:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def validate_manifest(manifest: dict[str, Any]) -> ManifestReport:
    conflicts: list[dict[str, Any]] = []
    sources = {row["key"]: row for row in manifest.get("sources", [])}
    entity_keys: set[str] = set()
    normalized: dict[tuple[str, str], str] = {}
    aliases: dict[tuple[str, str], str] = {}

    for source_key, source in sources.items():
        if not source.get("url", "").startswith("https://"):
            conflicts.append({"code": "invalid_source_url", "source": source_key})
        if not source.get("retrieved_at"):
            conflicts.append({"code": "missing_retrieved_at", "source": source_key})
        if source.get("source_type") not in ALLOWED_SOURCE_TYPES:
            conflicts.append({"code": "invalid_source_type", "source": source_key})

    for entity in manifest.get("entities", []):
        key = entity["key"]
        if key in entity_keys:
            conflicts.append({"code": "duplicate_entity_key", "entity": key})
        entity_keys.add(key)
        kind = entity["entity_type"]
        if kind not in ALLOWED_ENTITY_TYPES:
            conflicts.append({"code": "invalid_entity_type", "entity": key, "value": kind})
        identity = (kind, normalize_knowledge_text(entity["canonical_name"]))
        if not identity[1]:
            conflicts.append({"code": "empty_canonical_key", "entity": key})
        if previous := normalized.get(identity):
            conflicts.append({"code": "duplicate_canonical", "entity": key, "other": previous})
        normalized[identity] = key
        source_key = entity.get("source_key")
        if entity.get("review_status") == "approved" and source_key not in sources:
            conflicts.append({"code": "approved_without_source", "entity": key})
        for alias in entity.get("aliases", []):
            if alias.get("alias_type", "synonym") not in ALLOWED_ALIAS_TYPES:
                conflicts.append(
                    {"code": "invalid_alias_type", "entity": key, "alias": alias["value"]}
                )
            alias_key = (kind, normalize_knowledge_text(alias["value"]))
            if not alias_key[1]:
                conflicts.append({"code": "empty_alias_key", "entity": key, "alias": alias["value"]})
            if previous := aliases.get(alias_key):
                if previous != key:
                    conflicts.append(
                        {"code": "ambiguous_alias", "alias": alias["value"], "entities": [previous, key]}
                    )
            aliases[alias_key] = key

    for relation in manifest.get("relations", []):
        if relation.get("relation_type") not in ALLOWED_RELATION_TYPES:
            conflicts.append(
                {"code": "invalid_relation_type", "relation": relation}
            )
        for endpoint in ("subject", "object"):
            if relation[endpoint] not in entity_keys:
                conflicts.append(
                    {"code": "missing_relation_endpoint", "relation": relation, "endpoint": endpoint}
                )
        if relation.get("review_status") == "approved" and relation.get("source_key") not in sources:
            conflicts.append({"code": "approved_relation_without_source", "relation": relation})

    for relation in manifest.get("retire_relations", []):
        if relation.get("subject") not in entity_keys:
            conflicts.append({"code": "missing_retired_relation_subject", "relation": relation})
        if not relation.get("relation_type") or not relation.get("object_name"):
            conflicts.append({"code": "invalid_retired_relation", "relation": relation})

    for reassignment in manifest.get("reassign_coverage", []):
        for key in ("product", "manufacturer", "previous_manufacturer"):
            if reassignment.get(key) not in entity_keys:
                conflicts.append({"code": "missing_coverage_reassignment_entity", "field": key, "row": reassignment})

    for reassignment in manifest.get("reassign_product_coverage", []):
        for key in ("previous_product", "product"):
            if reassignment.get(key) not in entity_keys:
                conflicts.append(
                    {"code": "missing_product_coverage_reassignment_entity", "field": key, "row": reassignment}
                )
        if reassignment.get("previous_product") == reassignment.get("product"):
            conflicts.append({"code": "invalid_product_coverage_reassignment", "row": reassignment})

    for retirement in manifest.get("retire_entities", []):
        if retirement.get("entity") not in entity_keys:
            conflicts.append({"code": "missing_retired_entity", "row": retirement})
        if retirement.get("source_key") not in sources:
            conflicts.append({"code": "retired_entity_without_source", "row": retirement})
        if not retirement.get("reason"):
            conflicts.append({"code": "retired_entity_without_reason", "row": retirement})

    for retirement in manifest.get("retire_coverage", []):
        if not retirement.get("manufacturer_name") or not retirement.get("product_name"):
            conflicts.append({"code": "invalid_coverage_retirement", "row": retirement})
        if not retirement.get("reason"):
            conflicts.append({"code": "coverage_retirement_without_reason", "row": retirement})

    for row in manifest.get("applicability", []):
        for field in ("entity", "product"):
            if row.get(field) not in entity_keys:
                conflicts.append(
                    {"code": "missing_applicability_entity", "field": field, "row": row}
                )
        if row.get("status", "verified") not in ALLOWED_APPLICABILITY:
            conflicts.append({"code": "invalid_applicability_status", "row": row})
        if row.get("review_status") == "approved" and row.get("source_key") not in sources:
            conflicts.append({"code": "approved_applicability_without_source", "row": row})

    for row in manifest.get("coverage", []):
        for field in ("manufacturer", "product", "insert", "parallel"):
            if row.get(field) is not None and row[field] not in entity_keys:
                conflicts.append(
                    {"code": "missing_coverage_entity", "field": field, "row": row}
                )
        if row["status"] not in ALLOWED_COVERAGE:
            conflicts.append({"code": "invalid_coverage_status", "row": row})
        if row["status"] == "covered" and row.get("source_key") not in sources:
            conflicts.append({"code": "covered_without_source", "row": row})

    names = {
        entity["canonical_name"]
        for entity in manifest.get("entities", [])
    }
    names.update(alias["value"] for entity in manifest.get("entities", []) for alias in entity.get("aliases", []))
    required_regressions = set(manifest.get("required_regressions", REQUIRED_REGRESSIONS))
    replay = {term: term in names for term in sorted(required_regressions)}
    for term, passed in replay.items():
        if not passed:
            conflicts.append({"code": "required_regression_missing", "term": term})

    summary = {
        "sources": len(sources),
        "entities": len(manifest.get("entities", [])),
        "aliases": sum(len(row.get("aliases", [])) for row in manifest.get("entities", [])),
        "relations": len(manifest.get("relations", [])),
        "coverage_rows": len(manifest.get("coverage", [])),
        "retired_coverage_rows": len(manifest.get("retire_coverage", [])),
        "covered_rows": sum(row["status"] == "covered" for row in manifest.get("coverage", [])),
        "pending_source_rows": sum(
            row["status"] == "pending_source" for row in manifest.get("coverage", [])
        ),
    }
    return ManifestReport(
        checksum=manifest_checksum(manifest),
        summary=summary,
        conflicts=tuple(conflicts),
        replay=replay,
        publishable=not conflicts and all(replay.values()),
    )
