"""Replay a complete saved audit, optionally append new valuations through the worker.

No identity or historical valuation is overwritten. An account/card changed
since the frozen baseline is skipped. Missing audit files are a hard error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from card_platform.config import Settings
from card_platform.db import Database
from card_platform.domain.product_identity import ProductMatchStatus, assess_product_match
from card_platform.domain.valuation_quality import ShadowSale, ShadowValuationEstimator
from card_platform.ingestion.cardhobby import CardHobbySale, CardHobbySearchResult, cardhobby_item_id
from card_platform.market_data.cardhobby_provider import CardHobbyMarketDataProvider
from card_platform.market_data.models import MarketIdentity
from card_platform.valuation_worker import AutomatedValuationWorker, ComparableSearchResult
from scripts.review_valued_card_evidence import classification


def meaningful_image_conflicts(identity: dict, review: dict) -> list[dict]:
    """Model claims are review leads, never trusted solely on a confidence score."""
    result = []
    for conflict in review.get("conflicts", []):
        if not isinstance(conflict, dict):
            continue
        field = conflict.get("field")
        observed = conflict.get("observed")
        stored = identity.get(field)
        if observed in (None, "", "null", "unknown") or str(observed).casefold() == str(stored).casefold():
            continue
        if field in {"serial_number", "serial_total", "grade_value"} and stored is not None:
            try:
                if Decimal(str(observed)) == Decimal(str(stored)):
                    continue
            except ArithmeticError:
                pass
        if float(conflict.get("confidence") or 0) < 0.9:
            continue
        # A single copyright/statistics year is not release-season evidence.
        # Retain the original claim in images-v2, but never auto-correct from it.
        if field in {"season_start", "season_end"}:
            continue
        if field == "product_name" and assess_product_match(observed, stored) == ProductMatchStatus.MATCH:
            continue
        if field in {"insert_name", "parallel_name"} and str(observed).casefold() in {
            "rc", "rookie", "rookie card", "base", "prizm", "refractor", "chrome",
        }:
            continue
        if field == "player_name":
            def normalize(value):
                return "".join(c for c in str(value).casefold() if c.isalnum())
            if normalize(observed) == normalize(stored):
                continue
        result.append(conflict)
    return result


def evaluate(identity: dict, entries: list[dict]) -> dict:
    sales = []
    for entry in entries:
        source = CardHobbySale.model_validate(entry["source"])
        judged = classification(identity, {
            "listing_title": source.title, "source_code": "cardhobby",
            "grade_company": source.grade_company, "grade_value": source.grade_value,
            "provider_raw_payload": source.model_dump(mode="json"),
        }, entry.get("visual"))
        sales.append(ShadowSale(
            source.source_url, "cardhobby", source.sale_price, source.shipping_price,
            (datetime.now(UTC) - source.occurred_at).days, judged.tier,
            judged.similarity, judged.source_reliability, source.evidence_level,
        ))
    estimate = ShadowValuationEstimator().estimate(sales, identity, require_same_card=True)
    accepted = [sale.price for sale in sales if sale.id in estimate.used_ids]
    return {
        "fair": estimate.fair, "low": estimate.low, "high": estimate.high,
        "confidence": estimate.confidence, "used_urls": list(estimate.used_ids),
        "review_reasons": list(estimate.review_reasons),
        "independent_observed_range": [min(accepted), max(accepted)] if accepted else None,
        "range_basis": "card_only_observed_ended_auction_prices_not_confirmed_payments",
    }


async def run(directory: Path, apply: bool, partial: bool = False,
              previous_results: Path | None = None) -> None:
    baseline = json.loads((directory / "baseline.json").read_text())
    if apply and partial:
        raise ValueError("Partial audits cannot be published")
    if partial:
        baseline["items"] = [item for item in baseline["items"] if all(
            (directory / folder / f"{item['display_id']}.json").exists()
            for folder in ("images-v2", "evidence")
        )]
    for item in baseline["items"]:
        for folder in ("images-v2", "evidence"):
            if not (directory / folder / f"{item['display_id']}.json").exists():
                raise RuntimeError(f"Audit incomplete: {folder}/{item['display_id']}.json")
    new_by_card = {}
    for path in (directory / "new-evidence").glob("*.json"):
        for result in json.loads(path.read_text())["assessments"]:
            if result["accepted"]:
                new_by_card.setdefault(result["display_id"], []).append(result)
    recovered_pages = json.loads((directory / "revalidated-source-pages.json").read_text())
    settings = Settings(valuation_cardhobby_only=True, card_search_enabled=False)
    database = Database(settings)
    await database.open()
    worker = AutomatedValuationWorker(database, settings)
    output = directory / ("applied" if apply else "replayed")
    output.mkdir(exist_ok=True)
    try:
        for item in baseline["items"]:
            target = output / f"{item['display_id']}.json"
            if target.exists():
                continue
            reviewed = json.loads((directory / "evidence" / f"{item['display_id']}.json").read_text())
            identity = reviewed["identity"]
            old = []
            recovered = []
            for row in reviewed["evidence"]:
                if row["accepted_historical_candidate"] and row.get("source_record"):
                    old.append({"source": row["source_record"], "visual": row["visual"]})
                if not row.get("source_record"):
                    page = recovered_pages.get(cardhobby_item_id(row["source_url"])) or {}
                    visual = row.get("visual") or {}
                    if page.get("sale") and visual.get("verdict") == "same" and Decimal(
                        str(visual.get("confidence", "0"))
                    ) >= Decimal("0.85"):
                        recovered.append({"source": page["sale"], "visual": visual,
                                          "provenance": "first_party_item_page_recovered_source"})
            new_entries = [*new_by_card.get(item["display_id"], []), *recovered]
            all_entries = {cardhobby_item_id(entry["source"]["source_url"]): entry for entry in old}
            all_entries.update({cardhobby_item_id(entry["source"]["source_url"]): entry for entry in new_entries})
            entries = list(all_entries.values())
            images = json.loads((directory / "images-v2" / f"{item['display_id']}.json").read_text())
            image_findings = meaningful_image_conflicts(identity, images.get("review", {}))
            rounds = {"historical_reverified": evaluate(identity, old),
                      "including_new_cardhobby_pages": evaluate(identity, entries)}
            outcome = {
                "display_id": item["display_id"], "collection_item_id": item["collection_item_id"],
                "identity": identity, "baseline": item["baseline_snapshot"],
                "baseline_job_status": item["baseline_job"]["status"], "rounds": rounds,
                "image_findings_requiring_review": image_findings,
                "target_image_assessment": images.get("review", {}).get("identity_assessment"),
                "source_limitation": "CardHobby direct SearchCommodity unavailable; cached API facts and accessible first-party indexed pages only",
            }
            if apply:
                async with database.connection() as connection, connection.transaction():
                    row = await (await connection.execute(
                        """SELECT ci.deleted_at, (SELECT id FROM valuation_job
                           WHERE collection_item_id=ci.id ORDER BY created_at DESC LIMIT 1) latest_job
                           FROM collection_item ci WHERE ci.id=%s FOR UPDATE""",
                        (item["collection_item_id"],),
                    )).fetchone()
                    expected_job = item["baseline_job"]["id"]
                    if previous_results:
                        previous = json.loads((previous_results / f"{item['display_id']}.json").read_text())
                        if previous["collection_item_id"] != item["collection_item_id"]:
                            raise ValueError("Previous audit identity does not match frozen scope")
                        expected_job = previous["job"]["id"]
                    if row["deleted_at"] or str(row["latest_job"]) != expected_job:
                        outcome["apply_status"] = "skipped_concurrent_change"
                        target.write_text(json.dumps(outcome, default=str, ensure_ascii=False, indent=2))
                        continue
                    current = await worker._load_identity(UUID(item["baseline_job"]["id"]))
                    if (
                        MarketIdentity.model_validate(current).fingerprint != MarketIdentity.model_validate(identity).fingerprint
                        or any(current.get(key) != item.get(key) for key in ("image_front_url", "image_back_url"))
                        or str(current.get("confirmed_identity_version_id")) != str(identity.get("confirmed_identity_version_id"))
                    ):
                        outcome["apply_status"] = "skipped_identity_changed"
                        target.write_text(json.dumps(outcome, default=str, ensure_ascii=False, indent=2))
                        continue
                    job_id = uuid4()
                    await connection.execute(
                        """INSERT INTO valuation_job(id,collection_item_id,status,attempt_count,
                           started_at,result_summary) VALUES(%s,%s,'calculating',1,now(),%s)""",
                        (job_id, item["collection_item_id"], Jsonb({
                            "audit_run": directory.name, "source_policy": "cardhobby_only",
                            "audit_baseline_job_id": item["baseline_job"]["id"],
                        })),
                    )
                identity = current
                # A positive visual discrepancy needs review, not an invented
                # correction. Preserve the old snapshot as historical only.
                pricing_findings = [finding for finding in image_findings if finding["field"] != "serial_number"]
                if pricing_findings:
                    await worker._finish(job_id, "insufficient_evidence", {
                        "reason": "正反面复核发现待核验的身份字段，未自动更改身份或补值",
                        "audit_run": directory.name,
                        "identity_image_findings": pricing_findings,
                    })
                else:
                    selected = [entry for entry in entries if entry["source"]["source_url"] in rounds["including_new_cardhobby_pages"]["used_urls"]]
                    comparables = []
                    for entry in selected:
                        source = CardHobbySale.model_validate(entry["source"])
                        direct = CardHobbySearchResult(sales=[source])
                        # New records only: historical source facts are not
                        # overwritten by an audit reconstruction.
                        if entry in new_entries:
                            async with database.connection() as connection:
                                fingerprint = await worker.market_data.repository.ensure_identity(
                                    connection, MarketIdentity.model_validate(identity),
                                )
                                await worker.market_data.repository.store_evidence(
                                    connection, fingerprint,
                                    CardHobbyMarketDataProvider.normalize_result(direct).evidence,
                                )
                        visual = entry.get("visual") or {}
                        comparables.append(worker._cardhobby_comparables(direct)[0].model_copy(update={
                            "source_code": "cardhobby", "visual_verdict": visual.get("verdict"),
                            "visual_confidence": Decimal(str(visual.get("confidence", "0"))),
                            "visual_reasons": visual.get("reasons") or [],
                        }))
                    search = ComparableSearchResult(
                        comparables=comparables,
                        notes=["本轮逐卡复审：只使用卡淘；结束拍卖有出价不等于已确认付款。",
                               "卡淘直连接口不可用；使用保存的原始接口记录及本轮可读商品页，未宣称全量近期成交覆盖。"],
                    )
                    try:
                        await worker._store_comparables(identity, search)
                        await worker._calculate(job_id, identity, search)
                    except Exception as error:
                        await worker._finish(job_id, "failed", {"reason": "audit_replay_failed"}, str(error)[:400])
                async with database.connection() as connection:
                    job = await (await connection.execute(
                        "SELECT * FROM valuation_job WHERE id=%s", (job_id,),
                    )).fetchone()
                    outcome["job"] = dict(job)
                    valuation_id = (job["result_summary"] or {}).get("valuation_id")
                    if valuation_id:
                        snapshot = await (await connection.execute(
                            "SELECT * FROM valuation_snapshot WHERE id=%s", (valuation_id,),
                        )).fetchone()
                        outcome["new_snapshot"] = dict(snapshot) if snapshot else None
                outcome["apply_status"] = job["status"]
            target.write_text(json.dumps(outcome, default=str, ensure_ascii=False, indent=2))
            print(json.dumps({"display_id": item["display_id"], "status": outcome.get("apply_status", "replayed")}), flush=True)
    finally:
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--partial", action="store_true", help="Replay completed cards only; never publish")
    parser.add_argument("--previous-results", type=Path,
                        help="Append a corrective round only when its prior audit job is still current")
    args = parser.parse_args()
    asyncio.run(run(args.directory, args.apply, args.partial, args.previous_results))
