"""Recheck every frozen CardHobby comparable, with checkpointed image decisions.

Offline source records are always labeled historical, never a live re-fetch.
This script creates audit artifacts only; publication is a separate operation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from card_platform.comparable_image_verifier import ComparableImageVerifier, VisualCandidate
from card_platform.config import Settings
from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import ComparableClassifier, ComparableInput, ComparableTier
from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity


def classification(identity: dict, row: dict, visual: dict | None = None):
    visual = visual or {}
    preliminary = ComparableClassifier().classify(
        CardIdentityResolver().resolve(identity),
        ComparableInput(
            title=row["listing_title"] or "", source_code=row["source_code"],
            grade_company=row.get("grade_company"),
            grade_value=Decimal(str(row["grade_value"])) if row.get("grade_value") else None,
            visual_verdict=visual.get("verdict"),
            visual_confidence=Decimal(str(visual.get("confidence", "0"))),
            visual_reasons=tuple(visual.get("reasons") or []),
        ),
    )
    return enforce_formal_cardhobby_identity(
        identity, row.get("provider_raw_payload"), preliminary,
        visual_verdict=visual.get("verdict"),
    )


async def run(directory: Path, concurrency: int) -> None:
    baseline = json.loads((directory / "baseline.json").read_text())
    by_definition: dict[str, list] = {}
    for row in baseline["market_comps"]:
        by_definition.setdefault(row["card_definition_id"], []).append(row)
    output = directory / "evidence"
    output.mkdir(exist_ok=True)
    verifier = ComparableImageVerifier(Settings())
    semaphore = asyncio.Semaphore(concurrency)

    async def review(item):
        destination = output / f"{item['display_id']}.json"
        if destination.exists():
            return
        image_path = directory / "images-v2" / f"{item['display_id']}.json"
        while not image_path.exists():
            await asyncio.sleep(5)
        async with semaphore:
            if destination.exists():
                return
            image_review = json.loads(image_path.read_text())
            identity = image_review["identity"]
            rows = by_definition.get(item["card_definition_id"], [])
            cited = {
                str(value.get("source_url") or value.get("url"))
                for key in ("used_comparables", "references", "comparables")
                for value in item["baseline_snapshot"]["evidence"].get(key, [])
                if isinstance(value, dict)
            }
            candidates = [
                VisualCandidate(
                    id=row["id"], title=row["listing_title"] or "",
                    image_url=(row.get("provider_raw_payload") or {}).get("image_url")
                    or (row.get("raw_payload") or {}).get("image_url"),
                )
                for row in rows
                if (row.get("provider_raw_payload") or {}).get("image_url")
                or (row.get("raw_payload") or {}).get("image_url")
            ]
            checkpoint_dir = directory / "visual-checkpoints"
            checkpoint_dir.mkdir(exist_ok=True)
            checkpoint = checkpoint_dir / f"{item['display_id']}.json"
            decisions = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
            candidates = [candidate for candidate in candidates if candidate.id not in decisions]
            for start in range(0, len(candidates), verifier.MAX_TOTAL_CANDIDATES):
                try:
                    decisions.update(await verifier.verify(
                        item.get("image_front_url"),
                        candidates[start:start + verifier.MAX_TOTAL_CANDIDATES],
                        target_back_image_url=item.get("image_back_url"),
                        requires_parallel_match=str(identity.get("parallel_name") or "").lower()
                        not in {"", "base"},
                    ))
                except Exception as error:
                    for candidate in candidates[start:start + verifier.MAX_TOTAL_CANDIDATES]:
                        decisions[candidate.id] = {
                            "verdict": "uncertain", "confidence": "0",
                            "reasons": [f"review_error:{type(error).__name__}:{str(error)[:150]}"],
                        }
                checkpoint.write_text(json.dumps({
                    key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                    for key, value in decisions.items()
                }, ensure_ascii=False, indent=2))
            audited = []
            for row in rows:
                visual = decisions.get(row["id"])
                if visual is not None and hasattr(visual, "model_dump"):
                    visual = visual.model_dump(mode="json")
                before = classification(identity, row)
                after = classification(identity, row, visual)
                reasons = list(after.hard_conflicts)
                if row.get("currency") != "CNY" or Decimal(str(row.get("fx_rate_to_base") or 1)) != 1:
                    reasons.append("source_currency_or_fx_not_verifiable")
                occurred = datetime.fromisoformat(row["occurred_at"])
                age = (datetime.now(UTC) - occurred).days
                if age < 0 or age > 730:
                    reasons.append("outside_730_day_historical_window")
                if after.tier not in {ComparableTier.T0, ComparableTier.T1}:
                    reasons.append("not_exact_or_near_exact_same_grade")
                if not visual or visual.get("verdict") != "same" or Decimal(
                    str(visual.get("confidence", 0))
                ) < Decimal("0.85"):
                    reasons.append("same_version_thumbnail_not_confirmed")
                audited.append({
                    "market_comp_id": row["id"], "source_url": row["source_url"],
                    "raw_title": row["listing_title"], "occurred_at": row["occurred_at"],
                    "price": row["sale_price"], "shipping": row["shipping_price"],
                    "currency": row["currency"], "cited_by_baseline": row["source_url"] in cited,
                    "source_record": row.get("provider_raw_payload"),
                    "source_status": "historical_record_not_live_revalidated",
                    "visual": visual, "before_tier": before.tier.value,
                    "tier": after.tier.value, "field_matches": after.field_matches,
                    "accepted_historical_candidate": not reasons,
                    "reasons": list(dict.fromkeys(reasons)) or [
                        "historical_ended_auction_same_card_and_thumbnail;payment_unconfirmed",
                    ],
                })
            result = {
                "display_id": item["display_id"], "collection_item_id": item["collection_item_id"],
                "reviewed_at": datetime.now(UTC).isoformat(), "identity": identity,
                "evidence": audited,
                "unresolved_cited_urls": sorted(cited - {r["source_url"] for r in rows}),
            }
            destination.write_text(json.dumps(result, default=str, ensure_ascii=False, indent=2))
            print(json.dumps({"display_id": item["display_id"], "done": len(list(output.glob('*.json'))),
                              "candidates": len(rows), "historical_accepted": sum(
                                  r["accepted_historical_candidate"] for r in audited
                              )}), flush=True)

    try:
        await asyncio.gather(*(review(item) for item in baseline["items"]))
    finally:
        if verifier.client:
            await verifier.client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(run(args.directory, args.concurrency))
