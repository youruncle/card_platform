"""Read-only CardHobby recall/image audit for an explicitly reviewed identity.

Does not update identity, publish prices, or treat search counts as comparables.
The JSON input supplies display_id and identity; output is a new audit file.
"""
import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from card_platform.comparable_image_verifier import ComparableImageVerifier, VisualCandidate
from card_platform.config import Settings
from card_platform.db import Database
from card_platform.identity_review_api import _identity, _item_identity
from card_platform.ingestion.cardhobby import CardHobbyProvider
from scripts.review_valued_card_evidence import classification


async def run(input_path: Path, output: Path):
    plan = json.loads(input_path.read_text())
    if output.exists():
        raise ValueError("Refusing to overwrite an existing audit")
    settings = Settings()
    database = Database(settings)
    verifier = ComparableImageVerifier(settings)
    await database.open()
    try:
        async with database.connection() as connection:
            rows = await connection.execute(
                "SELECT id, owner_id FROM collection_item WHERE display_id=%s AND deleted_at IS NULL",
                (plan["display_id"],),
            )
            items = await rows.fetchall()
            if len(items) != 1:
                raise ValueError("display_id must identify exactly one active card")
            item = items[0]
            before = await _item_identity(connection, item["id"], item["owner_id"])
        identity = plan["identity"]
        search = await CardHobbyProvider(None, "unused", timeout=30).search(identity)
        result = {
            "checked_at": datetime.now(UTC).isoformat(), "display_id": plan["display_id"],
            "item_id": str(item["id"]), "before": _identity(before), "proposed": identity,
            "search": search.model_dump(mode="json"), "assessments": [],
        }
        # Persist discovery even if the image service fails subsequently.
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        visuals = await verifier.verify(
            before["image_front_url"],
            [VisualCandidate(s.item_id, s.title, s.image_url) for s in search.sales if s.image_url],
            target_back_image_url=before["image_back_url"], requires_parallel_match=True,
        )
        for sale in search.sales:
            visual = visuals.get(sale.item_id)
            payload = visual.model_dump(mode="json") if visual else None
            judged = classification(identity, {
                "listing_title": sale.title, "source_code": "cardhobby",
                "provider_raw_payload": sale.model_dump(mode="json"),
            }, payload)
            result["assessments"].append({
                "url": sale.source_url, "price": str(sale.sale_price), "visual": payload,
                "tier": judged.tier.value, "conflicts": list(judged.hard_conflicts),
            })
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        print(json.dumps(result["assessments"], ensure_ascii=False, indent=2))
    finally:
        await database.close()
        if verifier.client:
            await verifier.client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.input, args.output))
