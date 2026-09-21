"""Read-only frozen CardHobby replay. Never calls worker.run/_calculate or writes a DB.

Offline replay uses persisted visual observations, NOT a fresh independent vision verdict.
Use a fresh --out under work/. Production publication is deliberately not exposed here.
"""

from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from time import perf_counter

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import ComparableClassifier, ComparableInput
from card_platform.domain.valuation_quality import ShadowSale, ShadowValuationEstimator
from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity
from card_platform.valuation_trace import content_hash
from card_platform.valuation_worker import (
    AutomatedValuationWorker,
    revalidated_cardhobby_assessment,
    reconcile_cardhobby_classification,
)


def require_single_owner(owners):
    unique = set(owners)
    if len(unique) != 1:
        raise ValueError("exactly one explicit audit owner is required")
    return next(iter(unique))


@contextmanager
def readonly_connection(dsn):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(
        dsn,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=30000",
    ) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if (
            conn.execute("SHOW transaction_read_only").fetchone()[
                "transaction_read_only"
            ]
            != "on"
        ):
            raise RuntimeError("read-only transaction required")
        yield conn


def create_output(path, *, work_root):
    path, work_root = Path(path).resolve(), Path(work_root).resolve()
    if not path.is_relative_to(work_root) or path == work_root:
        raise ValueError("audit output must be a new private work subdirectory")
    path.mkdir(parents=True, exist_ok=False)
    return path


def resolve_frozen_identity(item, baseline):
    row = dict(item)
    row["image_content_hashes"] = item.get("image_hashes") or {}
    versions = [
        v
        for v in baseline.get("identity_versions", [])
        if str(v["collection_item_id"]) == str(item["collection_item_id"])
    ]
    revoked = {
        str(v.get("supersedes_id"))
        for v in versions
        if v["review_status"] == "rejected"
    }
    confirmed = [
        v
        for v in versions
        if v["review_status"] == "confirmed" and str(v["id"]) not in revoked
    ]
    version = max(confirmed, key=lambda v: v["version"], default=None)
    row["confirmed_identity"] = version["identity"] if version else None
    row["confirmed_identity_version_id"] = version["id"] if version else None
    row["scanner_recognition"] = next(
        (
            v["result"]
            for v in baseline.get("latest_scanner_results", [])
            if str(v["collection_item_id"]) == str(item["collection_item_id"])
        ),
        None,
    )
    worker = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
    worker.knowledge_runtime = None
    return worker.resolve_identity_row(row)


def validate_sample_scope(baseline, sample_set):
    items, samples = baseline["items"], sample_set["items"]
    ids = [r["display_id"] for r in items]
    sample_ids = [r["display_id"] for r in samples]
    if len(ids) != len(set(ids)) or len(sample_ids) != len(set(sample_ids)):
        raise ValueError("duplicate display id in frozen scope or sample set")
    if len({r["collection_item_id"] for r in items}) != len(items):
        raise ValueError("duplicate collection item in frozen scope")
    if not set(sample_ids).issubset(ids):
        raise ValueError("fixed sample missing from frozen scope")
    if any(r["split"] not in {"development", "holdout"} for r in samples):
        raise ValueError("invalid sample split")
    owners = {r.get("owner_id") for r in items}
    if owners != {None} and owners:
        if None in owners:
            raise ValueError("incomplete owner scope")
        require_single_owner(owners)


def validate_owner_attestation(baseline_bytes, attestation):
    if (
        attestation.get("baseline_sha256") != content_hash(baseline_bytes)
        or attestation.get("owner_count") != 1
        or not attestation.get("owner_fingerprint")
    ):
        raise ValueError("single-owner attestation must bind the exact frozen baseline")


def ordered_source_rows(rows):
    from card_platform.ingestion.cardhobby import CardHobbySale

    def key(row):
        try:
            CardHobbySale.model_validate(row.get("provider_raw_payload"))
            verifiable = True
        except (ValueError, TypeError):
            verifiable = False
        timestamp = row.get("evidence_fetched_at")
        try:
            epoch = datetime.fromisoformat(timestamp).timestamp() if timestamp else 0
        except (ValueError, TypeError):
            epoch = 0
        return (
            str(row.get("external_id") or row["source_url"]),
            verifiable,
            epoch,
            content_hash(row.get("provider_raw_payload")),
            str(row["id"]),
        )

    return sorted(rows, key=key, reverse=True)


def select_frozen_visual(current_raw, snapshot_visual):
    """Latest captured review wins; failure cannot erase a prior review requirement.

    A currently bound positive can resolve the retained quarantine in the shared
    validator, but missing/failed new observations cannot do so.
    """
    current_raw, snapshot_visual = current_raw or {}, snapshot_visual or {}
    has_current = any(
        key in current_raw
        for key in (
            "visual_verdict",
            "visual_evidence_binding",
            "visual_review_required",
        )
    )
    selected = dict(current_raw if has_current else snapshot_visual)
    from card_platform.comparable_image_verifier import has_visual_observation

    if (
        has_visual_observation(snapshot_visual)
        or current_raw.get("visual_verdict") == "different"
        or any(
            row.get("visual_review_required") is True
            for row in (current_raw, snapshot_visual)
        )
    ):
        selected["visual_review_required"] = True
    return selected


def replay(baseline, sample_set, *, split=None):
    validate_sample_scope(baseline, sample_set)
    selected = {
        r["display_id"]: r
        for r in sample_set["items"]
        if split is None or r["split"] == split
    }
    now = datetime.fromisoformat(baseline["frozen_at"])
    output = []
    for item in baseline["items"]:
        if item["display_id"] not in selected:
            continue
        start = perf_counter()
        identity = resolve_frozen_identity(item, baseline)
        target = CardIdentityResolver().resolve(identity)
        rows = [
            c
            for c in baseline["cardhobby_comps"]
            if c["card_definition_id"] == item["card_definition_id"]
        ]
        candidates, sales, seen = [], [], set()
        used_snapshot = {
            c.get("market_comp_id"): c
            for c in (item.get("latest_snapshot") or {})
            .get("evidence", {})
            .get("used_comparables", [])
        }
        for row in ordered_source_rows(rows):
            payload = row.get("provider_raw_payload")
            visual = select_frozen_visual(
                row.get("raw_payload"), used_snapshot.get(row["id"])
            )
            from card_platform.comparable_image_verifier import (
                current_persisted_visual,
                VisualCandidate,
            )

            visual = current_persisted_visual(
                identity,
                VisualCandidate(
                    row["source_url"],
                    row["listing_title"],
                    (payload or {}).get("image_url") or "",
                ),
                visual,
            )
            judged = ComparableClassifier().classify(
                target,
                ComparableInput(
                    title=row["listing_title"],
                    source_code="cardhobby",
                    grade_company=row.get("grade_company"),
                    grade_value=Decimal(str(row["grade_value"]))
                    if row.get("grade_value")
                    else None,
                    visual_verdict=visual.get("visual_verdict"),
                    visual_confidence=Decimal(
                        str(visual.get("visual_confidence") or 0)
                    ),
                    visual_reasons=tuple(visual.get("visual_reasons") or []),
                ),
            )
            tier, reasons = revalidated_cardhobby_assessment(identity, payload)
            judged, _ = reconcile_cardhobby_classification(
                judged, tier, provider_rejection_reasons=reasons
            )
            judged = enforce_formal_cardhobby_identity(
                identity,
                payload,
                judged,
                visual_verdict=visual.get("visual_verdict"),
                visual_review_required=visual.get("visual_review_required") is True,
            )
            key = str(row.get("external_id") or row["source_url"])
            duplicate = key in seen
            seen.add(key)
            candidates.append(
                {
                    "id": row["id"],
                    "item_key": key,
                    "url": row["source_url"],
                    "tier": judged.tier.value,
                    "reasons": list(judged.hard_conflicts),
                    "source_hash": content_hash(payload),
                    "duplicate": duplicate,
                    "visual_basis": "bound_image_review"
                    if visual.get("visual_evidence_binding")
                    else "historical_persisted_not_independently_revalidated",
                    "visual_evidence_binding": visual.get("visual_evidence_binding"),
                    "image_url": (payload or {}).get("image_url"),
                }
            )
            if duplicate:
                continue
            sales.append(
                ShadowSale(
                    id=key,
                    provider_code="cardhobby",
                    price=Decimal(str(row["sale_price"])),
                    shipping=Decimal(str(row["shipping_price"])),
                    age_days=(now - datetime.fromisoformat(row["occurred_at"])).days,
                    tier=judged.tier,
                    similarity=judged.similarity,
                    source_reliability=judged.source_reliability,
                    evidence_level=(payload or {}).get("evidence_level", "unknown"),
                )
            )
        result = ShadowValuationEstimator().estimate(
            sales, identity, require_same_card=True
        )
        output.append(
            {
                "display_id": item["display_id"],
                "split": selected[item["display_id"]]["split"],
                "input_identity": identity,
                "identity_hash": content_hash(identity),
                "input_image_hashes": item.get("image_hashes"),
                "status": result.status,
                "fair": result.fair,
                "low": result.low,
                "high": result.high,
                "used_ids": list(result.used_ids),
                "review_reasons": list(result.review_reasons),
                "candidates": candidates,
                "elapsed_ms": (perf_counter() - start) * 1000,
                "timing_scope": "offline_classification_and_calculation_only",
                "model_calls": 0,
                "model_cost": None,
                "cost_missing_reason": "no_live_pipeline_run",
                "publication_eligible": False,
            }
        )
    return output


async def shadow_retrieval(baseline, sample_set, *, split, max_pages=1):
    """Bounded public-source capture. No DB, model, stateful worker or valuation writes."""
    from card_platform.ingestion.cardhobby import CardHobbyProvider
    from card_platform.valuation_trace import trace_run

    validate_sample_scope(baseline, sample_set)
    selected = {r["display_id"] for r in sample_set["items"] if r["split"] == split}
    provider = CardHobbyProvider(
        None, "disabled", timeout=5, max_search_pages=max_pages
    )
    output = []
    consecutive_failures = 0
    for item in baseline["items"]:
        if item["display_id"] not in selected:
            continue
        if consecutive_failures >= 3:
            output.append(
                {
                    "display_id": item["display_id"],
                    "blocked_reason": "source_circuit_open",
                    "reference_interval": None,
                    "production_writeback": False,
                }
            )
            continue
        events = []
        identity = resolve_frozen_identity(item, baseline)
        started = perf_counter()
        with trace_run(
            events.append, card_id=str(item["display_id"]), max_model_calls=0
        ):
            result = await provider._search_direct(identity)
        failed = bool(result.query_diagnostics) and all(
            d.outcome == "error" for d in result.query_diagnostics
        )
        consecutive_failures = consecutive_failures + 1 if failed else 0
        output.append(
            {
                "display_id": item["display_id"],
                "identity_hash": content_hash(identity),
                "result": result.model_dump(mode="json"),
                "events": events,
                "elapsed_ms": (perf_counter() - started) * 1000,
                "scope": "retrieval_only",
                "reference_interval": None,
                "model_calls": 0,
                "model_cost": None,
                "cost_missing_reason": "models_disabled_retrieval_only",
                "production_writeback": False,
            }
        )
    return output


async def visual_shadow_card(item, baseline, retrieval, settings):
    """Fresh paired recognition and visual verification; no DB or identity/price publication."""
    from card_platform.comparable_image_verifier import (
        ComparableImageVerifier,
        VisualCandidate,
    )
    from card_platform.image_service import (
        QwenVisionProvider,
        detect_image_content_type,
    )
    from card_platform.valuation_trace import trace_run, emit

    identity = resolve_frozen_identity(item, baseline)
    verifier = ComparableImageVerifier(settings)
    events, images, hashes = [], [], {}
    for side in ("front", "back"):
        path = verifier._target_path(identity.get("image_" + side + "_url"))
        if path and path.stat().st_size <= verifier.MAX_IMAGE_BYTES:
            data = path.read_bytes()
            content_type = detect_image_content_type(data)
            if content_type:
                images.append((data, content_type))
                hashes[side] = content_hash(data)
    output = {
        "display_id": item["display_id"],
        "identity_hash": content_hash(identity),
        "image_hashes": hashes,
        "recognition_status": "unavailable",
        "same_card_status": "unavailable",
        "model_cost": None,
        "cost_missing_reason": "versioned_provider_price_not_configured",
        "publication_eligible": False,
        "events": events,
    }
    started = perf_counter()
    with trace_run(events.append, card_id=str(item["display_id"])) as run:
        emit(
            "identity",
            "resolved",
            input_hash=content_hash(item),
            output_hash=content_hash(identity),
            source="shared_resolver_confirmed_then_scanner",
            knowledge_aliases="not_loaded",
        )
        from card_platform.ingestion.cardhobby import CardHobbyProvider

        emit(
            "alias_query",
            "planned",
            queries=CardHobbyProvider.build_query_plan(identity),
        )
        if images and settings.vision_api_key:
            provider = QwenVisionProvider(
                settings.vision_api_key,
                settings.vision_base_url,
                settings.vision_model,
                settings.vision_timeout_seconds,
            )
            try:
                recognition = await provider.recognize_images(
                    images,
                    "图片按可用正面、背面顺序提供。独立读取原卡身份；名单编号、统计年、球衣号不得当作卡号或发行年。缺背面或不可读字段保留未知。",
                )
                output.update(
                    recognition_status="succeeded",
                    recognition=recognition.model_dump(mode="json"),
                )
            except Exception as error:
                output.update(
                    recognition_status="failed",
                    recognition_error_type=type(error).__name__,
                )
            finally:
                await provider.client.close()
        sales = retrieval.get("result", {}).get("sales", [])
        candidates = [
            VisualCandidate(sale["source_url"], sale["title"], sale["image_url"])
            for sale in sales
            if sale.get("image_url")
        ]
        decisions = {}
        if candidates and images and settings.vision_api_key:
            try:
                decisions = await verifier.verify(
                    identity.get("image_front_url"),
                    candidates,
                    target_back_image_url=identity.get("image_back_url"),
                    requires_parallel_match=bool(identity.get("parallel_name")),
                    identity=identity,
                )
                output["same_card_status"] = "succeeded" if decisions else "unavailable"
            except Exception as error:
                output.update(
                    same_card_status="failed", same_card_error_type=type(error).__name__
                )
        if verifier.client is not None:
            await verifier.client.close()
        output["visual_decisions"] = {
            key: value.model_dump(mode="json") for key, value in decisions.items()
        }
        comps = []
        prior_by_key = {
            str(row.get("external_id") or "").removeprefix("item:"): row.get(
                "raw_payload"
            )
            or {}
            for row in reversed(
                ordered_source_rows(baseline.get("cardhobby_comps", []))
            )
            if row["card_definition_id"] == item["card_definition_id"]
        }
        for sale in sales:
            decision = decisions.get(sale["source_url"])
            raw = (
                {
                    "visual_verdict": decision.verdict,
                    "visual_confidence": str(decision.confidence),
                    "visual_reasons": decision.reasons,
                    "visual_evidence_binding": decision.evidence_binding,
                }
                if decision
                else {"visual_verdict": None}
            )
            raw = select_frozen_visual(raw, prior_by_key.get(str(sale.get("item_id"))))
            comps.append(
                {
                    "id": content_hash(sale["source_url"]),
                    "external_id": sale.get("item_id"),
                    "card_definition_id": item["card_definition_id"],
                    "source_url": sale["source_url"],
                    "listing_title": sale["title"],
                    "grade_company": sale.get("grade_company"),
                    "grade_value": sale.get("grade_value"),
                    "sale_price": sale["sale_price"],
                    "shipping_price": sale.get("shipping_price", "0"),
                    "occurred_at": sale["occurred_at"],
                    "provider_raw_payload": sale,
                    "raw_payload": raw,
                }
            )
        live = {
            **baseline,
            "items": [{**item, "latest_snapshot": None}],
            "cardhobby_comps": comps,
        }
        output["valuation"] = replay(
            live,
            {"items": [{"display_id": item["display_id"], "split": "development"}]},
        )[0]
        emit(
            "price_calculation",
            "completed",
            output_hash=content_hash(output["valuation"]),
            source="shadow_estimator",
            publication_eligible=False,
        )
        output["model_calls"] = run.model_calls
    output["elapsed_ms"] = (perf_counter() - started) * 1000
    output["scope"] = (
        "frozen_effective_identity_with_fresh_recognition_diagnostic_and_cached_retrieval"
    )
    return output


def reference_metrics(rows, labels):
    """Metrics on independently sufficient references, never historical ranges."""
    from math import ceil
    from statistics import median

    by_id = {row["display_id"]: row for row in labels}
    if len(by_id) != len(labels):
        raise ValueError("duplicate reference label")
    if len({row["display_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate result")
    distances, sufficient, reasonable = [], 0, 0
    overlaps, widths = [], []
    for row in rows:
        label = by_id.get(row["display_id"], {})
        interval = label.get("reference_interval")
        if label.get("reference_status") != "sufficient" or interval is None:
            continue
        low, high = map(Decimal, map(str, interval))
        if not low.is_finite() or not high.is_finite() or not 0 < low <= high:
            raise ValueError("invalid independent reference interval")
        sufficient += 1
        if row.get("low") is not None and row.get("high") is not None:
            system_low, system_high = (
                Decimal(str(row["low"])),
                Decimal(str(row["high"])),
            )
            if (
                not system_low.is_finite()
                or not system_high.is_finite()
                or not 0 < system_low <= system_high
            ):
                raise ValueError("invalid system interval")
            intersection = max(
                Decimal(0), min(high, system_high) - max(low, system_low)
            )
            union = max(high, system_high) - min(low, system_low)
            overlaps.append(float(intersection / union) if union else 1.0)
            widths.append(float((system_high - system_low) / ((low + high) / 2)))
        if row.get("fair") is None:
            continue
        fair = Decimal(str(row["fair"]))
        if not fair.is_finite() or fair <= 0:
            raise ValueError("invalid price")
        distance = (
            (low - fair) / low
            if fair < low
            else (fair - high) / high
            if fair > high
            else Decimal(0)
        )
        distances.append(float(distance))
        reasonable += distance == 0
    distances.sort()
    return {
        "N": len(rows),
        "D": sufficient,
        "U": len(rows) - sufficient,
        "priced_reference_count": len(distances),
        "reasonable_coverage": reasonable / sufficient if sufficient else None,
        "interval_distance_median": median(distances) if distances else None,
        "interval_distance_p90": distances[ceil(0.9 * len(distances)) - 1]
        if distances
        else None,
        "interval_compared_count": len(overlaps),
        "interval_overlap_mean": sum(overlaps) / len(overlaps) if overlaps else None,
        "normalized_width_mean": sum(widths) / len(widths) if widths else None,
        "price_acceptance_evaluable": sufficient >= 20,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--samples", type=Path, required=True)
    p.add_argument("--owner-attestation", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--mode", choices=["offline", "shadow-retrieval"], default="offline")
    p.add_argument("--max-pages", type=int, default=1)
    p.add_argument("--split", choices=["development", "holdout"], required=True)
    args = p.parse_args()
    baseline = json.loads(args.baseline.read_text())
    samples = json.loads(args.samples.read_text())
    validate_owner_attestation(
        args.baseline.read_bytes(), json.loads(args.owner_attestation.read_text())
    )
    validate_sample_scope(baseline, samples)
    counts = {
        split: sum(r["split"] == split for r in samples["items"])
        for split in ("development", "holdout")
    }
    if counts != {"development": 40, "holdout": 20}:
        raise ValueError(
            "approved fixed sample requires 40 development and 20 holdout cards"
        )
    out = create_output(args.out, work_root=Path.cwd() / "work")
    if args.mode == "offline":
        rows = replay(baseline, samples, split=args.split)
    else:
        import asyncio

        rows = asyncio.run(
            shadow_retrieval(
                baseline, samples, split=args.split, max_pages=args.max_pages
            )
        )
    (out / "results.json").write_text(
        json.dumps(rows, default=str, ensure_ascii=False, indent=2) + "\n"
    )
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "baseline_sha256": content_hash(args.baseline.read_bytes()),
                "samples_sha256": content_hash(args.samples.read_bytes()),
                "split": args.split,
                "cards": len(rows),
                "mode": args.mode,
                "max_pages": args.max_pages if args.mode != "offline" else 0,
                "production_writeback": False,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            {
                "cards": len(rows),
                "priced": sum(r.get("fair") is not None for r in rows),
                "production_writeback": False,
            }
        )
    )


if __name__ == "__main__":
    main()
