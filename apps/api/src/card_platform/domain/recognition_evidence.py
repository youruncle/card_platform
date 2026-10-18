"""Field proposals are not confirmations; storage defaults are not observations.

Only the scanner creates a storage binding. Consumers use it to recover missing
logical values while the exact identity and image bytes still match. Historical
unbound recognition must never undo a later correction.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from card_platform.schemas import RecognitionFieldAssessment

if TYPE_CHECKING:
    from card_platform.schemas import CardRecognitionResult

FIELDS = (
    "player_name",
    "brand_name",
    "product_name",
    "season_start",
    "season_end",
    "card_number",
    "insert_name",
    "parallel_name",
    "serial_number",
    "serial_total",
    "is_numbered",
    "is_rookie",
    "is_autograph",
    "is_memorabilia",
)
CATALOG_FIELDS = {
    "player_name",
    "brand_name",
    "product_name",
    "season_start",
    "season_end",
    "card_number",
    "insert_name",
    "parallel_name",
}
FIELD_ROLE_ISSUES = {
    'serial_number_has_set_position_role', 'serial_total_has_set_position_role',
    'serial_number_has_print_quantity_role', 'serial_total_has_print_quantity_role',
    "card_number_is_location_not_catalog_identifier",
    "card_number_is_calendar_date_not_catalog_identifier",
    "card_number_is_fraction_not_catalog_identifier",
    "card_number_evidence_has_non_catalog_role",
    "season_start_not_direct_release_evidence",
    "season_end_not_direct_release_evidence",
    "product_name_inferred_from_design_not_product_label",
    "copyright_year_not_in_quoted_evidence",
}
# Include inventory-only fields and flags because editing these also changes
# the owned copy. Store strings consistently across UUID/Decimal and JSON I/O.
BINDING_FIELDS = (
    "card_definition_id",
    *FIELDS,
    "grade_company",
    "grade_value",
    "set_name",
    "print_run",
    "autograph_type",
    "has_inscription",
    "inscription_text",
    "is_patch",
    "memorabilia_types",
    "memorabilia_usage",
    "memorabilia_player",
)
PLACEHOLDERS = {
    "无法识别球员",
    "未识别球员",
    "未知球员",
    "无法识别品牌",
    "无法识别产品",
    "unknown",
    "unspecified",
    "未识别",
    "未知",
}


def assess_recognition_fields(result: CardRecognitionResult) -> CardRecognitionResult:
    # Import here to keep schemas, routing and enrichment free of import cycles.
    from card_platform.card_search_service import candidate_supports_resolved_field

    assessments = {}
    markers = set(result.recognition_quality_issues) | set(result.caveats)
    for field in FIELDS:
        value = getattr(result, field)
        evidence = list(result.field_evidence.get(field, []))
        issues = sorted(
            marker
            for marker in markers
            if marker in FIELD_ROLE_ISSUES and marker.startswith(field + "_")
        )
        missing = (
            value is None
            or value == ""
            or (isinstance(value, str) and value.casefold().strip() in PLACEHOLDERS)
        )
        verified = (
            not missing
            and field in CATALOG_FIELDS
            and result.catalog_verified_fields.get(field) == value
            and any(text.startswith("目录核验：") for text in evidence)
            and any(
                candidate_supports_resolved_field(candidate, field, value)
                for candidate in result.web_candidates
            )
        )
        ocr_observed = (
            not missing
            and field
            in {
                "season_start",
                "season_end",
                "card_number",
                "serial_number",
                "serial_total",
                "is_numbered",
                "is_rookie",
                "is_autograph",
                "is_memorabilia",
            }
            and result.local_ocr_observed_fields.get(field) == value
        )
        if verified or (ocr_observed and field not in {"season_start", "season_end"}):
            # Historical rejection stays in caveats; it no longer rejects an
            # independently sourced replacement for the current value.
            issues = []
        assessments[field] = RecognitionFieldAssessment(
            value=None if missing else value,
            status="conflicted"
            if issues
            else "unknown"
            if missing
            else "verified"
            if verified
            else "proposed",
            source="catalog"
            if verified
            else "local_ocr"
            if ocr_observed
            else "visual_model"
            if not missing or evidence
            else "none",
            evidence=evidence,
            issues=issues,
        )
    return result.model_copy(update={"field_assessments": assessments})


def _storage_binding(card: dict[str, Any]) -> dict[str, Any]:
    hashes = card.get("image_content_hashes") or {}
    if (
        not card.get("card_definition_id")
        or not isinstance(hashes, dict)
        or not re.fullmatch(r"[0-9a-f]{64}", str(hashes.get("front") or ""))
        or any(not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in hashes.values())
    ):
        return {}
    return {
        "version": 1,
        "stored_values": {
            field: None if card.get(field) is None else str(card[field]) for field in BINDING_FIELDS
        },
        "image_content_hashes": dict(hashes),
    }


def bind_recognition_storage(
    result: CardRecognitionResult, card: dict[str, Any]
) -> CardRecognitionResult:
    result = assess_recognition_fields(result)
    return result.model_copy(update={"identity_storage_binding": _storage_binding(card)})


def purchase_identity_view(
    card: dict[str, Any], recognition: dict[str, Any] | None
) -> dict[str, Any]:
    if card.get("has_confirmed_identity"):
        return card
    binding = _storage_binding(card)
    if not recognition or not binding or recognition.get("identity_storage_binding") != binding:
        return card
    # Recompute rather than trusting a provider's assessment labels. This view
    # only removes unknown/conflicted values; it never fills a replacement.
    from card_platform.schemas import CardRecognitionResult
    from pydantic import ValidationError

    try:
        result = assess_recognition_fields(CardRecognitionResult.model_validate(recognition))
    except (ValidationError, TypeError, ValueError):
        return card
    updates = {
        field: None
        for field, state in result.field_assessments.items()
        if state.status in {"unknown", "conflicted"}
    }
    return {**card, **updates}
