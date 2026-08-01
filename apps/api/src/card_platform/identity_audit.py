import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.domain.card_identity import INSERT_ALIASES, normalize_text


DETECTOR_VERSION = "market-title-identity-1.0"
PARALLEL_ALIASES = {
    "Cur": ("cur", "curry"),
    "Red": ("red", "红平行"),
    "Blue": ("blue", "蓝平行"),
    "Gold": ("gold", "金平行"),
    "Silver": ("silver", "银平行"),
    "Green": ("green", "绿平行"),
    "Purple": ("purple", "紫平行"),
}
AUTOGRAPH_PATTERN = re.compile(r"签字|卡签|贴签|签名|\bauto(?:graph)?\b|\brpa\b", re.I)
PATCH_PATTERN = re.compile(r"球衣|物料|切割|patch|jersey|relic|\brpa\b", re.I)
EXPLICIT_CARD_NUMBER = re.compile(
    r"(?:卡号|card\s*(?:no\.?|number)|#|no\.?)\s*[:：#]?\s*([a-z0-9][a-z0-9-]{0,15})\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class IdentitySuggestion:
    field_name: str
    current_value: Any
    suggested_value: Any
    confidence: Decimal
    evidence: list[dict[str, Any]]


def _contains(text: str, value: object) -> bool:
    normalized = normalize_text(value)
    return bool(normalized and normalized in normalize_text(text))


def _canonical_from_aliases(
    title: str, aliases_by_canonical: dict[str, tuple[str, ...]]
) -> str | None:
    normalized = normalize_text(title)
    matches = [
        canonical
        for canonical, aliases in aliases_by_canonical.items()
        if any(normalize_text(alias) in normalized for alias in aliases)
    ]
    return matches[0] if len(matches) == 1 else None


def extract_identity_fields(title: str, current: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    insert = _canonical_from_aliases(title, INSERT_ALIASES)
    if insert:
        fields["insert_name"] = insert.title()
    parallel = _canonical_from_aliases(title, PARALLEL_ALIASES)
    if parallel:
        fields["parallel_name"] = parallel
    explicit_number = EXPLICIT_CARD_NUMBER.search(title)
    if explicit_number:
        fields["card_number"] = explicit_number.group(1)
    elif (
        insert
        and parallel
        and normalize_text(insert) == normalize_text(current.get("insert_name"))
        and normalize_text(parallel) == normalize_text(current.get("parallel_name"))
    ):
        terminal = re.search(r"(?:】|\])\s*([a-z0-9][a-z0-9-]{0,11})\s*$", title, re.I)
        if terminal:
            fields["card_number"] = terminal.group(1)
    totals = {
        int(value)
        for pair in re.findall(
            r"(?:/\s*(\d+)(?!\d)|(?<!\d)(\d+)\s*[编遍])",
            title,
        )
        for value in pair
        if value
    }
    if len(totals) == 1:
        fields["serial_total"] = next(iter(totals))
    fields["is_autograph"] = bool(AUTOGRAPH_PATTERN.search(title))
    fields["is_patch"] = bool(PATCH_PATTERN.search(title))
    return fields


def build_identity_suggestions(
    current: dict[str, Any], comparables: list[dict[str, Any]]
) -> list[IdentitySuggestion]:
    structural_suggestions: list[IdentitySuggestion] = []
    card_number = str(current.get("card_number") or "").strip()
    fraction = re.fullmatch(r"0*(\d+)\s*/\s*0*(\d+)", card_number)
    if fraction:
        structural_suggestions.append(
            IdentitySuggestion(
                field_name="card_number",
                current_value=current.get("card_number"),
                suggested_value=None,
                confidence=Decimal("0.99"),
                evidence=[
                    {
                        "reason": "分数形式是序列限编，不是图鉴卡号",
                        "serial_number": int(fraction.group(1)),
                        "serial_total": int(fraction.group(2)),
                    }
                ],
            )
        )
    votes: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    values: dict[tuple[str, str], Any] = {}
    for comp in comparables:
        title = str(comp.get("listing_title") or "")
        source_url = str(comp.get("source_url") or "")
        if not (
            _contains(title, current.get("player_name"))
            and _contains(title, current.get("product_name"))
            and (not current.get("season_start") or str(current["season_start"]) in title)
            and urlparse(source_url).scheme in {"http", "https"}
        ):
            continue
        extracted = extract_identity_fields(title, current)
        for field_name, value in extracted.items():
            key = normalize_text(value)
            values[(field_name, key)] = value
            votes[field_name][key].append(
                {
                    "title": title,
                    "source_url": source_url,
                    "market_comp_id": str(comp.get("id")),
                    "source_reliability": str(comp.get("source_reliability") or "0.70"),
                    "match_confidence": str(comp.get("match_confidence") or "0.50"),
                }
            )
    suggestions = list(structural_suggestions)
    for field_name, candidates in votes.items():
        ordered = sorted(candidates.items(), key=lambda item: len(item[1]), reverse=True)
        best_key, evidence = ordered[0]
        if len(ordered) > 1 and len(ordered[1][1]) == len(evidence):
            continue
        suggested = values[(field_name, best_key)]
        current_value = current.get(field_name)
        if normalize_text(current_value) == normalize_text(suggested):
            continue
        if field_name in {"serial_total", "is_autograph", "is_patch"} and len(evidence) < 2:
            continue
        confidence = min(Decimal("0.98"), Decimal("0.82") + Decimal("0.06") * len(evidence))
        suggestions.append(
            IdentitySuggestion(
                field_name=field_name,
                current_value=current_value,
                suggested_value=suggested,
                confidence=confidence,
                evidence=evidence,
            )
        )
    return suggestions


class IdentityAuditRepository:
    async def replace_open_suggestions(
        self,
        connection: AsyncConnection,
        collection_item_id,
        suggestions: list[IdentitySuggestion],
    ) -> None:
        fields = [suggestion.field_name for suggestion in suggestions]
        await connection.execute(
            """
            UPDATE card_identity_correction
            SET status = 'superseded', updated_at = now()
            WHERE collection_item_id = %s AND status = 'open'
              AND NOT (field_name = ANY(%s))
            """,
            (collection_item_id, fields),
        )
        for suggestion in suggestions:
            await connection.execute(
                """
                INSERT INTO card_identity_correction (
                    collection_item_id, field_name, current_value, suggested_value,
                    confidence, evidence, detector_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (collection_item_id, field_name) WHERE status = 'open'
                DO UPDATE SET
                    current_value = EXCLUDED.current_value,
                    suggested_value = EXCLUDED.suggested_value,
                    confidence = EXCLUDED.confidence,
                    evidence = EXCLUDED.evidence,
                    detector_version = EXCLUDED.detector_version,
                    detected_at = now(), updated_at = now()
                """,
                (
                    collection_item_id,
                    suggestion.field_name,
                    Jsonb(suggestion.current_value),
                    Jsonb(suggestion.suggested_value),
                    suggestion.confidence,
                    Jsonb(suggestion.evidence),
                    DETECTOR_VERSION,
                ),
            )
