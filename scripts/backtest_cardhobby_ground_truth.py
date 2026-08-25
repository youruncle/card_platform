#!/usr/bin/env python3
"""Run CardHobby retrieval against reviewed valuation benchmark identities."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from card_platform.ingestion.cardhobby import CardHobbyProvider


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARKS = sorted(
    (ROOT / "data" / "benchmarks").glob("valuation-ground-truth-*.json")
)


def load_cases(paths: list[Path]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text())
        cases.extend({**case, "benchmark_file": path.name} for case in payload["cases"])
    return cases


async def run(cases: list[dict[str, Any]]) -> dict[str, Any]:
    provider = CardHobbyProvider(None, "unused", timeout=20)
    results: list[dict[str, Any]] = []
    for case in cases:
        result = await provider._search_direct(case["identity"])
        exact = [
            sale
            for sale in result.sales
            if "cardhobby_tier:exact_or_near_exact" in sale.matching_fields
        ]
        analog = [
            sale
            for sale in result.sales
            if "cardhobby_tier:same_player_product_analog" in sale.matching_fields
        ]
        results.append(
            {
                "display_id": case["display_id"],
                "benchmark_file": case["benchmark_file"],
                "queries": result.search_queries,
                "exact_count": len(exact),
                "analog_count": len(analog),
                "sales": [
                    {
                        "item_id": sale.item_id,
                        "title": sale.title,
                        "landed_price": str(sale.sale_price + sale.shipping_price),
                        "tier": next(
                            (
                                field.removeprefix("cardhobby_tier:")
                                for field in sale.matching_fields
                                if field.startswith("cardhobby_tier:")
                            ),
                            None,
                        ),
                        "match_confidence": str(sale.match_confidence),
                        "conflicts": sale.conflicts,
                        "source_url": sale.source_url,
                    }
                    for sale in result.sales
                ],
                "notes": result.notes,
            }
        )
    return {
        "summary": {
            "case_count": len(results),
            "cases_with_exact": sum(row["exact_count"] > 0 for row in results),
            "cases_with_analog": sum(row["analog_count"] > 0 for row in results),
            "cases_without_candidates": sum(
                not row["exact_count"] and not row["analog_count"] for row in results
            ),
        },
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("benchmarks", type=Path, nargs="*")
    args = parser.parse_args()
    cases = load_cases(args.benchmarks or DEFAULT_BENCHMARKS)
    print(json.dumps(asyncio.run(run(cases)), ensure_ascii=False))


if __name__ == "__main__":
    main()
