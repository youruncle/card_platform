#!/usr/bin/env python3
"""Compare reviewed valuation ground truth with one account's current DB state."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import unicodedata
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARKS = sorted(
    (ROOT / "data" / "benchmarks").glob("valuation-ground-truth-*.json")
)
IDENTITY_FIELDS = (
    "player_name",
    "season_start",
    "season_end",
    "product_name",
    "card_number",
    "insert_name",
    "parallel_name",
    "serial_number",
    "serial_total",
    "is_rookie",
    "is_autograph",
    "is_memorabilia",
    "is_patch",
)


PRODUCT_NOISE = {"panini", "topps", "basketball", "nba", "the"}


def normalized(value: Any) -> Any:
    if isinstance(value, str):
        value = unicodedata.normalize("NFKD", value.casefold())
        value = "".join(character for character in value if not unicodedata.combining(character))
        return " ".join(re.findall(r"[a-z0-9]+", value)) or None
    return value


def identity_values_match(field: str, expected: Any, actual: Any) -> bool:
    if field in {"is_rookie", "is_autograph", "is_memorabilia", "is_patch"}:
        return bool(expected) == bool(actual)
    expected_normalized = normalized(expected)
    actual_normalized = normalized(actual)
    if expected_normalized == actual_normalized:
        return True
    if field != "product_name" or not expected_normalized or not actual_normalized:
        return False
    expected_tokens = set(expected_normalized.split()) - PRODUCT_NOISE
    actual_tokens = set(actual_normalized.split()) - PRODUCT_NOISE
    return bool(expected_tokens and actual_tokens) and (
        expected_tokens <= actual_tokens or actual_tokens <= expected_tokens
    )


def load_cases(paths: list[Path]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text())
        for case in payload["cases"]:
            cases.append({
                **case,
                "benchmark_file": path.name,
                "valuation_basis": payload.get("valuation_basis", "card_only"),
            })
    return cases


def typical_shipping(case: dict[str, Any]) -> float:
    """Return a transparent benchmark-basis adjustment, never a sale price."""
    values: list[float] = []
    for evidence in case.get("evidence") or []:
        shipping = evidence.get("shipping")
        if isinstance(shipping, (int, float)):
            values.append(float(shipping))
        shipping_range = evidence.get("shipping_range")
        if (
            isinstance(shipping_range, list)
            and len(shipping_range) == 2
            and all(isinstance(value, (int, float)) for value in shipping_range)
        ):
            values.append(sum(map(float, shipping_range)) / 2)
    return statistics.median(values) if values else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-id", type=UUID, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("benchmarks", type=Path, nargs="*")
    args = parser.parse_args()
    database_url = args.database_url or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    cases = load_cases(args.benchmarks or DEFAULT_BENCHMARKS)
    display_ids = [case["display_id"] for case in cases]
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT ci.display_id, ci.id AS collection_item_id,
                   p.full_name AS player_name, cs.season_start, cs.season_end,
                   cs.product_name, cd.card_number, cd.insert_name, cd.parallel_name,
                   ci.serial_number, ci.serial_total, cd.is_rookie, cd.is_autograph,
                   cd.is_patch,
                   confirmed.identity AS confirmed_identity,
                   job.status AS valuation_status, job.result_summary,
                   shadow.status AS shadow_status,
                   shadow.fair_value AS shadow_fair_value,
                   snapshot.fair_value, snapshot.low_value, snapshot.high_value,
                   snapshot.confidence AS valuation_confidence
            FROM collection_item ci
            JOIN card_definition cd ON cd.id = ci.card_definition_id
            JOIN card_set cs ON cs.id = cd.card_set_id
            JOIN player p ON p.id = cd.player_id
            LEFT JOIN LATERAL (
                SELECT identity FROM card_identity_version
                WHERE collection_item_id = ci.id AND review_status = 'confirmed'
                ORDER BY version DESC LIMIT 1
            ) confirmed ON true
            LEFT JOIN LATERAL (
                SELECT status, result_summary FROM valuation_job
                WHERE collection_item_id = ci.id ORDER BY created_at DESC LIMIT 1
            ) job ON true
            LEFT JOIN LATERAL (
                SELECT status, fair_value FROM valuation_shadow_result
                WHERE collection_item_id = ci.id
                  AND valuation_job_id = (
                      SELECT id FROM valuation_job
                      WHERE collection_item_id = ci.id
                      ORDER BY created_at DESC LIMIT 1
                  )
                  AND identity_version_id = (
                      SELECT id FROM card_identity_version
                      WHERE collection_item_id = ci.id AND review_status = 'confirmed'
                      ORDER BY version DESC LIMIT 1
                  )
                ORDER BY created_at DESC LIMIT 1
            ) shadow ON true
            LEFT JOIN LATERAL (
                SELECT fair_value, low_value, high_value, confidence
                FROM valuation_snapshot WHERE collection_item_id = ci.id
                  AND identity_compatibility <> 'incompatible'
                ORDER BY valued_at DESC LIMIT 1
            ) snapshot ON true
            WHERE ci.owner_id = %s AND ci.deleted_at IS NULL
              AND ci.display_id = ANY(%s)
            """,
            (args.owner_id, display_ids),
        ).fetchall()
    current_by_display = {row["display_id"]: row for row in rows}
    report: list[dict[str, Any]] = []
    for case in cases:
        current = current_by_display.get(case["display_id"])
        if current is None:
            report.append({
                "display_id": case["display_id"],
                "status": "missing_collection_item",
                "benchmark_file": case["benchmark_file"],
            })
            continue
        current_identity = current["confirmed_identity"] or current
        differences = {
            field: {
                "ground_truth": case["identity"].get(field),
                "current": current_identity.get(field),
            }
            for field in IDENTITY_FIELDS
            if field in case["identity"]
            and not identity_values_match(
                field,
                case["identity"].get(field),
                current_identity.get(field),
            )
        }
        published_prediction = (
            current["fair_value"]
            if current["valuation_status"] == "succeeded"
            else None
        )
        shadow_prediction = (
            current["shadow_fair_value"]
            if current["shadow_status"] == "estimated"
            else None
        )
        prediction = (
            published_prediction
            if published_prediction is not None
            else shadow_prediction
        )
        shipping_adjustment = (
            typical_shipping(case)
            if "including_typical_domestic_shipping" in case["valuation_basis"]
            else 0.0
        )
        comparable_prediction = (
            float(prediction) + shipping_adjustment
            if prediction is not None
            else None
        )
        ground_truth_fair = case["ground_truth"]["fair"]
        report.append(
            {
                "display_id": case["display_id"],
                "collection_item_id": str(current["collection_item_id"]),
                "benchmark_file": case["benchmark_file"],
                "identity_confirmed": current["confirmed_identity"] is not None,
                "identity_mismatch": bool(differences),
                "identity_differences": differences,
                "valuation_status": current["valuation_status"],
                "shadow_status": current["shadow_status"],
                "prediction_source": (
                    "published"
                    if published_prediction is not None
                    else "shadow"
                    if shadow_prediction is not None
                    else None
                ),
                "system_fair": str(prediction) if prediction is not None else None,
                "valuation_basis": case["valuation_basis"],
                "shipping_adjustment": shipping_adjustment,
                "system_fair_ground_truth_basis": comparable_prediction,
                "ground_truth_fair": ground_truth_fair,
                "absolute_percentage_error": (
                    round(
                        abs(comparable_prediction - ground_truth_fair) / ground_truth_fair,
                        4,
                    )
                    if comparable_prediction is not None and ground_truth_fair
                    else None
                ),
                "failure_category": (
                    "identity_mismatch"
                    if differences
                    else "identity_unconfirmed"
                    if current["confirmed_identity"] is None
                    else "valuation_missing"
                    if prediction is None
                    else "shadow_valued"
                    if published_prediction is None
                    else "valued"
                ),
                "result_summary": current["result_summary"],
            }
        )
    summary = {
        "case_count": len(report),
        "identity_mismatch_count": sum(
            bool(row.get("identity_mismatch")) for row in report
        ),
        "identity_unconfirmed_count": sum(
            row.get("identity_confirmed") is False for row in report
        ),
        "valuation_missing_count": sum(
            row.get("failure_category") == "valuation_missing" for row in report
        ),
        "shadow_valued_count": sum(
            row.get("failure_category") == "shadow_valued" for row in report
        ),
        "valued_count": sum(row.get("failure_category") == "valued" for row in report),
        "missing_collection_item_count": sum(
            row.get("status") == "missing_collection_item" for row in report
        ),
    }
    errors = [
        row["absolute_percentage_error"]
        for row in report
        if row.get("absolute_percentage_error") is not None
    ]
    summary.update(
        {
            "scored_count": len(errors),
            "mean_absolute_percentage_error": (
                round(statistics.fmean(errors), 4) if errors else None
            ),
            "median_absolute_percentage_error": (
                round(statistics.median(errors), 4) if errors else None
            ),
            "within_20_percent_count": sum(error <= 0.2 for error in errors),
            "severe_error_over_50_percent_count": sum(error > 0.5 for error in errors),
        }
    )
    print(json.dumps({"summary": summary, "cases": report}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
