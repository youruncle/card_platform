"""Paired-image verification of newly discovered, dated CardHobby item pages."""
import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path

from card_platform.comparable_image_verifier import ComparableImageVerifier, VisualCandidate
from card_platform.config import Settings
from scripts.parse_cardhobby_review_sources import parse_reader_item
from scripts.review_valued_card_evidence import classification


async def run(directory: Path):
    plans = {row["display_id"]: row for row in json.loads((directory / "search-plan.json").read_text())}
    verifier = ComparableImageVerifier(Settings())
    semaphore = asyncio.Semaphore(4)
    output = directory / "new-evidence"
    output.mkdir(exist_ok=True)

    async def review(path):
        destination = output / path.name
        if destination.exists():
            return
        async with semaphore:
            page = json.loads(path.read_text())
            parsed = parse_reader_item(page["page"])
            result = {**page, "parsed": parsed, "assessments": []}
            if parsed and parsed.get("sale") and page.get("image_url"):
                image_url = page["image_url"].rstrip(":")
                source = {**parsed["sale"], "image_url": image_url}
                for display_id in sorted(set(page["matches"])):
                    identity = plans[display_id]["identity"]
                    decisions = await verifier.verify(
                        identity.get("image_front_url"),
                        [VisualCandidate(page["item_id"], source["title"], image_url)],
                        target_back_image_url=identity.get("image_back_url"),
                        requires_parallel_match=bool(identity.get("parallel_name")),
                    )
                    decision = decisions.get(page["item_id"])
                    visual = decision.model_dump(mode="json") if decision else None
                    row = {"listing_title": source["title"], "source_code": "cardhobby",
                           "provider_raw_payload": source}
                    judged = classification(identity, row, visual)
                    reasons = list(judged.hard_conflicts)
                    if judged.tier.value not in {"T0", "T1"}:
                        reasons.append("same_card_and_grade_not_confirmed")
                    if not visual or visual["verdict"] != "same" or Decimal(
                        str(visual["confidence"])
                    ) < Decimal("0.85"):
                        reasons.append("same_version_thumbnail_not_confirmed")
                    result["assessments"].append({
                        "display_id": display_id, "source": source, "visual": visual,
                        "tier": judged.tier.value, "accepted": not reasons,
                        "reasons": reasons,
                    })
            destination.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps({"item_id": page["item_id"], "accepted": sum(
                a["accepted"] for a in result["assessments"]
            )}), flush=True)

    try:
        await asyncio.gather(*(review(path) for path in (directory / "new-source-pages").glob('*.json')))
    finally:
        if verifier.client:
            await verifier.client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    asyncio.run(run(parser.parse_args().directory))
