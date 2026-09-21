"""Extract auditable price facts from saved CardHobby page-reader results.

Only item pages qualify. Feedback bundles, dollar conversions, snippets lacking
dated bids, future endings and listings remain discovery-only records.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from card_platform.ingestion.cardhobby import CardHobbySale, assess_cardhobby_candidate
from card_platform.domain.listing_title import GRADING_DETAIL_PATTERN


def parse_reader_item(text: str, *, now: datetime | None = None) -> dict | None:
    header = re.search(
        r"^([^\n]+) \((https://(?:www\.)?cardhobby\.com\.cn/market/item/(\d+)/?)\)",
        text, re.I | re.M,
    )
    if not header:
        return None
    cleaned = re.sub(r"cite[^†]*†([^]*)", r"\1", text)
    cleaned = re.sub(r"^L\d+:\s*", "", cleaned, flags=re.M)
    ended = re.search(r"结束时间\s*\|\s*\|\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", cleaned)
    price = re.search(r"当前价\s*\|\s*\|\s*([\d,]+(?:\.\d+)?)", cleaned)
    bids = re.search(r"当前价[^\n]*?\b(\d+)\s*次出价", cleaned)
    result = {
        "item_id": header[3], "source_url": header[2],
        "raw_title": re.sub(r"\s+卡淘$", "", header[1]),
        "currency": "CNY", "source_page_text": text,
        "payment_confirmed": False, "reasons": [],
    }
    if not (ended and price and bids):
        result["reasons"].append("item_page_lacks_dated_positive_bid_price")
        return result
    occurred = datetime.strptime(ended[1], "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    value = Decimal(price[1].replace(",", ""))
    result.update(occurred_at=occurred.isoformat(), price=str(value), bid_count=int(bids[1]))
    if occurred > (now or datetime.now(UTC)) or int(bids[1]) <= 0 or value <= 0:
        result["reasons"].append("not_a_positive_bid_past_ended_auction")
        return result
    grade = GRADING_DETAIL_PATTERN.search(result["raw_title"])
    result["sale"] = CardHobbySale(
        item_id=header[3], title=result["raw_title"], source_url=header[2],
        occurred_at=occurred, sale_price=value, bid_count=int(bids[1]),
        sale_type="auction", evidence_level="ended_auction", match_confidence=Decimal("0.5"),
        sold_status_evidence=f"卡淘商品页结束时间已过且有{bids[1]}次出价；未确认付款",
        grade_company=grade.group(1).upper() if grade else None,
        grade_value=Decimal(grade.group(2)) if grade else None,
    ).model_dump(mode="json")
    return result


def run(directory: Path) -> None:
    plans = {row["display_id"]: row for row in json.loads((directory / "search-plan.json").read_text())}
    results = {}
    for path in sorted((directory / "web-search").glob("*.json")):
        batch = json.loads(path.read_text())
        for block in batch["raw_result"].split("-" * 80):
            candidate = parse_reader_item(block.strip())
            if candidate is None:
                continue
            candidate["retrieved_at"] = batch["searched_at"]
            candidate["discovery_queries"] = batch["queries"]
            candidate["identity_assessments"] = []
            if candidate.get("sale"):
                sale = CardHobbySale.model_validate(candidate["sale"])
                for query in batch["queries"]:
                    score, conflicts, tier = assess_cardhobby_candidate(
                        plans[query["display_id"]]["identity"], sale,
                    )
                    candidate["identity_assessments"].append({
                        "display_id": query["display_id"], "score": str(score),
                        "tier": tier, "reasons": conflicts,
                        "image_review": "pending_new_candidate_thumbnail",
                    })
            old = results.get(candidate["item_id"])
            if old:
                assessments = candidate["identity_assessments"] + old["identity_assessments"]
                queries = candidate["discovery_queries"] + old["discovery_queries"]
                if old.get("sale") and not candidate.get("sale"):
                    candidate = old
                candidate["identity_assessments"] = assessments
                candidate["discovery_queries"] = queries
            results[candidate["item_id"]] = candidate
    (directory / "new-web-candidates.json").write_text(json.dumps(
        list(results.values()), ensure_ascii=False, indent=2,
    ))
    print(json.dumps({"unique_item_pages": len(results), "with_dated_bid_price": sum(
        bool(value.get("sale")) for value in results.values()
    ), "exact_title_matches": sum(
        any(a["tier"] == "exact_or_near_exact" for a in value["identity_assessments"])
        for value in results.values()
    )}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    run(parser.parse_args().directory)
