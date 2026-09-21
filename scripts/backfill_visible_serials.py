#!/usr/bin/env python3
"""Apply explicitly reviewed, source-hash-bound serial repairs; dry-run by default.

This changes only missing owned-copy serial fields and the corresponding scanner
projection. It never changes a shared card definition or a confirmed identity.
Original scanner values/evidence remain embedded in its recovery audit route.
Revaluation is deliberately separate so the new estimator can be deployed first.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from card_platform.config import Settings
from card_platform.domain.serial_evidence import (
    unique_visible_serial,
    visible_serial_evidence,
)
from card_platform.recognition_benchmark_predictor import canonicalize_identity
from card_platform.schemas import CardRecognitionResult

SERIAL_FIELDS = ("is_numbered", "serial_number", "serial_total")
MIN_VISUAL_REVIEW_NOTE_LENGTH = 20


def source_hashes(record: dict) -> dict[str, str]:
    images = record.get("source_images", [])
    if len(images) != 2 or {image.get("side") for image in images} != {
        "front",
        "back",
    }:
        raise ValueError("a complete front/back source pair is required")
    hashes = {}
    for image in images:
        digest = image.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("each source image requires a sha256 digest")
        hashes[image["side"]] = digest.casefold()
    return hashes


def validate_approval_target(record: dict, approval: dict) -> None:
    for field in ("owner_id", "collection_item_id"):
        if not approval.get(field) or approval[field] != record.get(field):
            raise ValueError(f"approval {field} does not match the audited record")
    if approval.get("display_id") != record.get("display_id"):
        raise ValueError("approval display_id does not match the audited record")
    number, total = approval.get("serial_number"), approval.get("serial_total")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or not 0 < number <= total
    ):
        raise ValueError("approval requires a valid integer serial pair")
    approved_hashes = approval.get("source_sha256")
    if not isinstance(approved_hashes, dict) or {
        str(side): str(digest).casefold() for side, digest in approved_hashes.items()
    } != source_hashes(record):
        raise ValueError("approval source hashes do not match the audited record")


def source_bound_observations(record: dict, approval: dict) -> list[dict]:
    validate_approval_target(record, approval)
    images = record["source_images"]
    hashes = source_hashes(record)
    observations = record.get("ocr", {}).get("accepted_observations", [])
    if not observations:
        raise ValueError("source OCR did not contain an accepted observation")
    for observation in observations:
        index = observation.get("image_index")
        side = observation.get("side")
        digest = observation.get("source_sha256")
        if (
            not isinstance(index, int)
            or not 0 <= index < len(images)
            or side not in hashes
            or images[index].get("side") != side
            or not isinstance(digest, str)
            or digest.casefold() != hashes[side]
        ):
            raise ValueError("OCR observation is not bound to its audited source image")
    return observations


def observation_stamp(observation: dict):
    """Revalidate one OCR read without letting nearby alternate reads replace it."""

    text = observation.get("text")
    context = observation.get("context")
    if not isinstance(text, str) or not isinstance(context, str):
        return None
    stamp = unique_visible_serial([text], source_is_physical=True)
    supported = visible_serial_evidence([context], source_is_physical=True)
    if stamp is None or not any(
        (candidate.number, candidate.total) == (stamp.number, stamp.total)
        for candidate in supported
    ):
        return None
    return stamp


def observation_evidence(observation: dict) -> str:
    side_label = "正面" if observation["side"] == "front" else "背面"
    return (
        f"Apple Vision 实卡{side_label} OCR，旋转 "
        f"{observation.get('orientation_degrees', 0)}°，"
        f"{observation.get('read_pass', 'full')}，"
        f"区域 {observation.get('bbox', [])}：{observation['text']}"
    )


def reviewed_stamp(record: dict, approval: dict):
    note = approval.get("visual_review_note")
    if not isinstance(note, str) or len(note.strip()) < MIN_VISUAL_REVIEW_NOTE_LENGTH:
        raise ValueError("a sufficiently detailed pixel review note is required")
    if record["protection"]["protected"]:
        raise ValueError("identity history is protected")
    validate_approval_target(record, approval)
    approved_pair = (approval["serial_number"], approval["serial_total"])
    if approval.get("manual_visual_only") is True:
        if approval.get("resolved_conflicting_observations") is True:
            raise ValueError(
                "manual-only and conflicting-OCR review modes are exclusive"
            )
        side = approval.get("proof_side")
        digest = approval.get("proof_source_sha256")
        if side not in {"front", "back"} or digest != source_hashes(record)[side]:
            raise ValueError(
                "manual visual proof is not bound to an audited source image"
            )
        visual_evidence = approval.get("visual_evidence")
        if not isinstance(visual_evidence, str):
            raise ValueError("manual visual proof requires verbatim x/y evidence")
        stamp = unique_visible_serial([visual_evidence], source_is_physical=True)
        if stamp is None or (stamp.number, stamp.total) != approved_pair:
            raise ValueError(
                "manual visual evidence does not contain the approved pair"
            )
        side_label = "正面" if side == "front" else "背面"
        return stamp, [f"人工原图逐像素核验（{side_label}）：{visual_evidence}"]

    observations = source_bound_observations(record, approval)
    evidence_by_pair: dict[tuple[int, int], list[str]] = {}
    stamp_by_pair = {}
    for observation in observations:
        stamp = observation_stamp(observation)
        if stamp is None:
            continue
        pair = (stamp.number, stamp.total)
        stamp_by_pair[pair] = stamp
        evidence_by_pair.setdefault(pair, []).append(observation_evidence(observation))

    if approved_pair not in evidence_by_pair:
        raise ValueError("reviewed pair is not present in source-bound OCR")
    if (
        len(evidence_by_pair) > 1
        and approval.get("resolved_conflicting_observations") is not True
    ):
        raise ValueError(
            "conflicting OCR requires an explicit visual-review resolution"
        )
    if len(evidence_by_pair) == 1:
        all_evidence = next(iter(evidence_by_pair.values()))
        unique_stamp = unique_visible_serial(all_evidence, source_is_physical=True)
        if (
            unique_stamp is None
            or (
                unique_stamp.number,
                unique_stamp.total,
            )
            != approved_pair
        ):
            raise ValueError("reviewed pair is not uniquely supported by source OCR")
    selected_evidence = list(dict.fromkeys(evidence_by_pair[approved_pair]))
    if not selected_evidence:
        raise ValueError("reviewed pair is not uniquely supported by source OCR")
    return stamp_by_pair[approved_pair], selected_evidence


def approved_records(report: dict, approvals: list[dict]) -> list[tuple[dict, dict]]:
    """Resolve every approval before the first database write."""

    records: dict[tuple[str, str], dict] = {}
    for record in report.get("items", []):
        key = (record.get("owner_id"), record.get("collection_item_id"))
        if not all(isinstance(value, str) and value for value in key):
            raise ValueError("audited record is missing its owner/item identity")
        if key in records:
            raise ValueError("audit contains duplicate owner/item identities")
        records[key] = record

    resolved = []
    seen: set[tuple[str, str]] = set()
    for approval in approvals:
        key = (approval.get("owner_id"), approval.get("collection_item_id"))
        if not all(isinstance(value, str) and value for value in key):
            raise ValueError("approval requires owner_id and collection_item_id")
        if key in seen:
            raise ValueError("approval contains a duplicate owner/item target")
        record = records.get(key)
        if record is None:
            raise ValueError("approval target is not present in the audit")
        reviewed_stamp(record, approval)
        resolved.append((record, approval))
        seen.add(key)
    return resolved


def repaired_scanner_result(original: dict, record: dict, approval: dict) -> dict:
    stamp, evidence = reviewed_stamp(record, approval)
    result = deepcopy(original)
    previous = {
        "fields": {field: result.get(field) for field in SERIAL_FIELDS},
        "field_evidence": {
            field: result.get("field_evidence", {}).get(field, [])
            for field in SERIAL_FIELDS
        },
        "caveats": result.get("caveats", []),
    }
    result.update(
        is_numbered=True, serial_number=stamp.number, serial_total=stamp.total
    )
    fields = result.setdefault("field_evidence", {})
    for field in SERIAL_FIELDS:
        # The old observations are retained below, but are no longer the
        # current evidence after a source-bound pixel review resolves them.
        fields[field] = list(evidence)
    result["caveats"] = [
        text
        for text in result.get("caveats", [])
        if not text.startswith("限编缺少图片中可见的 x/y 分数证据")
    ]
    result.setdefault("recognition_route", []).append(
        {
            "tier": "serial_recovery",
            "model": "visual_audit"
            if approval.get("manual_visual_only")
            else "apple_vision",
            "source": (
                "source_bound_manual_visual_audit"
                if approval.get("manual_visual_only")
                else "source_bound_visual_review"
            ),
            "status": "succeeded",
            "version": "2026-09-09-v2",
            "repaired_at": datetime.now(UTC).isoformat(),
            "before": previous,
            "source_images": record["source_images"],
            "observations": record["ocr"]["raw_observations"],
            "visual_review_note": approval["visual_review_note"],
            "resolved_conflicting_observations": approval.get(
                "resolved_conflicting_observations", False
            ),
            "manual_visual_only": approval.get("manual_visual_only", False),
            "manual_visual_proof": (
                {
                    "side": approval.get("proof_side"),
                    "source_sha256": approval.get("proof_source_sha256"),
                    "visual_evidence": approval.get("visual_evidence"),
                }
                if approval.get("manual_visual_only")
                else None
            ),
        }
    )
    return result


def apply_one(
    connection, settings, record: dict, approval: dict, *, apply: bool
) -> dict:
    stamp, _ = reviewed_stamp(record, approval)
    outcome = {
        "display_id": record["display_id"],
        "owner_id": record["owner_id"],
        "collection_item_id": record["collection_item_id"],
    }
    with connection.transaction():
        # Recheck under row locks, including edits made after the audit began.
        item = connection.execute(
            """
            SELECT ci.*, cd.print_run FROM collection_item ci
            JOIN card_definition cd ON cd.id = ci.card_definition_id
            WHERE ci.id = %s AND ci.owner_id = %s AND ci.deleted_at IS NULL
            FOR UPDATE OF ci
        """,
            (record["collection_item_id"], record["owner_id"]),
        ).fetchone()
        if item is None:
            return {**outcome, "status": "skipped", "reason": "item_unavailable"}
        if (
            item["serial_number"] is not None
            or item["serial_total"] is not None
            or item["is_numbered"]
            or item["print_run"] is not None
        ):
            return {**outcome, "status": "skipped", "reason": "serial_already_set"}
        if item["updated_at"] != datetime.fromisoformat(
            record["current"]["updated_at"]
        ):
            return {
                **outcome,
                "status": "skipped",
                "reason": "item_changed_after_audit",
            }
        job = connection.execute(
            """
            SELECT * FROM scanner_recognition_job WHERE id = %s
            AND collection_item_id = %s AND status = 'succeeded' FOR UPDATE
        """,
            (record["scanner_job"]["id"], item["id"]),
        ).fetchone()
        if job is None:
            return {**outcome, "status": "skipped", "reason": "scanner_job_changed"}
        protected = connection.execute(
            """
            SELECT EXISTS(SELECT 1 FROM card_identity_change_audit WHERE collection_item_id = %s)
                OR EXISTS(SELECT 1 FROM card_identity_version WHERE collection_item_id = %s
                    AND (confirmed_by IS NOT NULL OR (review_status = 'confirmed' AND created_at >= %s)))
                OR EXISTS(SELECT 1 FROM card_identity_correction WHERE collection_item_id = %s
                    AND field_name = ANY(%s) AND status = 'applied') AS protected
        """,
            (
                item["id"],
                item["id"],
                job["imported_at"],
                item["id"],
                list(SERIAL_FIELDS),
            ),
        ).fetchone()
        if protected["protected"]:
            return {
                **outcome,
                "status": "skipped",
                "reason": "protected_identity_history",
            }
        if len(record.get("source_images", [])) != 2 or {
            image.get("side") for image in record["source_images"]
        } != {"front", "back"}:
            return {
                **outcome,
                "status": "skipped",
                "reason": "original_pair_incomplete",
            }
        source_root = Path(settings.scanner_job_dir).resolve()
        for image in record["source_images"]:
            side = image["side"]
            if (
                item[f"image_{side}_url"] != record[f"image_{side}_url"]
                or job[f"{side}_original_image_path"] != image["relative_path"]
            ):
                return {**outcome, "status": "skipped", "reason": "source_changed"}
            path = (source_root / image["relative_path"]).resolve()
            if (
                source_root not in path.parents
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != image["sha256"]
            ):
                return {
                    **outcome,
                    "status": "skipped",
                    "reason": "source_hash_mismatch",
                }
        original = job["result"]
        patched = repaired_scanner_result(original, record, approval)
        # Rehearse the real read path before writing. Metadata formatting or
        # future normalization changes must not silently clear an approved
        # stamp the next time scanner output is loaded.
        normalized = canonicalize_identity(CardRecognitionResult.model_validate(patched))
        if (
            normalized.is_numbered is not True
            or normalized.serial_number != stamp.number
            or normalized.serial_total != stamp.total
        ):
            raise ValueError("approved serial does not survive scanner normalization")
        before = {field: item[field] for field in SERIAL_FIELDS}
        after = {
            "is_numbered": True,
            "serial_number": stamp.number,
            "serial_total": stamp.total,
        }
        outcome.update(
            before=before,
            after=after,
            source_images=record["source_images"],
            visual_review_note=approval["visual_review_note"],
            scanner_result_before=original,
            status="applied" if apply else "dry_run",
        )
        if apply:
            connection.execute(
                """UPDATE collection_item SET is_numbered=true,
                serial_number=%s, serial_total=%s, updated_at=now() WHERE id=%s""",
                (stamp.number, stamp.total, item["id"]),
            )
            connection.execute(
                """UPDATE scanner_recognition_job SET result=%s, updated_at=now()
                WHERE id=%s""",
                (Jsonb(patched), job["id"]),
            )
        return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--approvals", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = json.loads(Path(args.audit).read_text())
    approvals = json.loads(Path(args.approvals).read_text())
    resolved = approved_records(report, approvals)
    settings = Settings()
    results = []
    output = Path(args.output)
    if output.exists():
        raise ValueError("refusing to overwrite an existing backfill audit")
    output.parent.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        for record, approval in resolved:
            results.append(
                apply_one(
                    connection,
                    settings,
                    record,
                    approval,
                    apply=args.apply,
                )
            )
            output.write_text(
                json.dumps(
                    {"applied": args.apply, "items": results},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
    print(
        json.dumps(
            [
                {
                    key: value
                    for key, value in result.items()
                    if key in {"display_id", "status", "reason", "after"}
                }
                for result in results
            ],
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
