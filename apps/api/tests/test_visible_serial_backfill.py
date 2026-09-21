from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, UTC
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "serial_backfill", Path(__file__).resolve().parents[3] / "scripts/backfill_visible_serials.py"
)
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)

FRONT_SHA = hashlib.sha256(b"front source").hexdigest()
BACK_SHA = hashlib.sha256(b"back source").hexdigest()


def fixture_record():
    observation = {
        "image_index": 1,
        "side": "back",
        "source_sha256": BACK_SHA,
        "text": "194/249",
        "context": "PRIZM\n194/249\n2025-26 PANINI",
        "read_pass": "full",
    }
    return {
        "display_id": 9,
        "collection_item_id": "item",
        "owner_id": "owner",
        "protection": {"protected": False},
        "source_images": [
            {"side": "front", "relative_path": "front.jpg", "sha256": FRONT_SHA},
            {"side": "back", "relative_path": "back.jpg", "sha256": BACK_SHA},
        ],
        "scanner_job": {"id": "job"},
        "current": {"updated_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat()},
        "ocr": {"accepted_observations": [observation], "raw_observations": [observation]},
    }


APPROVAL = {
    "display_id": 9,
    "owner_id": "owner",
    "collection_item_id": "item",
    "source_sha256": {"front": FRONT_SHA, "back": BACK_SHA},
    "serial_number": 194,
    "serial_total": 249,
    "visual_review_note": "Source pixels show a separate foil stamp.",
}


def test_backfill_does_not_accept_unreviewed_or_mismatching_numbers():
    record = fixture_record()
    with pytest.raises(ValueError):
        backfill.reviewed_stamp(record, {**APPROVAL, "serial_total": 99})
    with pytest.raises(ValueError):
        backfill.reviewed_stamp(record, {**APPROVAL, "visual_review_note": ""})
    record["protection"]["protected"] = True
    with pytest.raises(ValueError):
        backfill.reviewed_stamp(record, APPROVAL)


def test_source_coordinate_evidence_survives_real_scanner_canonicalization():
    from card_platform.recognition_benchmark_predictor import canonicalize_identity
    from card_platform.schemas import CardRecognitionResult

    record = fixture_record()
    record["ocr"]["accepted_observations"][0]["bbox"] = [0.1280995, 0.5349821, 0.0588, 0.1425]
    stored = backfill.repaired_scanner_result({"confidence": "0.8"}, record, APPROVAL)
    result = canonicalize_identity(CardRecognitionResult.model_validate(stored))
    assert (result.is_numbered, result.serial_number, result.serial_total) == (True, 194, 249)


@pytest.mark.parametrize(
    "mutation",
    [
        {"side": "front"},
        {"source_sha256": "f" * 64},
        {"image_index": 0},
    ],
)
def test_accepted_observation_must_match_its_source_side_and_hash(mutation):
    record = fixture_record()
    record["ocr"]["accepted_observations"][0].update(mutation)

    with pytest.raises(ValueError, match="bound to its audited source"):
        backfill.reviewed_stamp(record, APPROVAL)


def test_conflicting_ocr_requires_explicit_review_and_cannot_invent_a_pair():
    record = fixture_record()
    conflicting = {
        **record["ocr"]["accepted_observations"][0],
        "text": "194/299",
        "context": "PRIZM\n194/299\n2025-26 PANINI",
    }
    record["ocr"]["accepted_observations"].append(conflicting)
    record["ocr"]["raw_observations"].append(conflicting)

    with pytest.raises(ValueError, match="explicit visual-review resolution"):
        backfill.reviewed_stamp(record, APPROVAL)
    reviewed = {
        **APPROVAL,
        "resolved_conflicting_observations": True,
        "visual_review_note": (
            "Full source pixels clearly show 194/249; the 299 read is an OCR error."
        ),
    }
    stamp, evidence = backfill.reviewed_stamp(record, reviewed)
    assert (stamp.number, stamp.total) == (194, 249)
    assert evidence and all("194/249" in item for item in evidence)
    with pytest.raises(ValueError, match="not present in source-bound OCR"):
        backfill.reviewed_stamp(
            record,
            {**reviewed, "serial_number": 195, "serial_total": 249},
        )
    with pytest.raises(ValueError, match="sufficiently detailed"):
        backfill.reviewed_stamp(record, {**reviewed, "visual_review_note": "looks right"})


def test_manual_visual_only_is_hash_bound_and_not_labeled_as_ocr():
    record = fixture_record()
    record["ocr"] = {"accepted_observations": [], "raw_observations": []}
    approval = {
        **APPROVAL,
        "serial_number": 65,
        "serial_total": 125,
        "manual_visual_only": True,
        "proof_side": "back",
        "proof_source_sha256": BACK_SHA,
        "visual_evidence": "背面左侧竖排原图清晰可见 065/125",
        "visual_review_note": "逐字核对未增强原图，065/125 为卡面独立竖排印编。",
    }

    stamp, evidence = backfill.reviewed_stamp(record, approval)
    assert (stamp.number, stamp.total) == (65, 125)
    assert evidence == ["人工原图逐像素核验（背面）：背面左侧竖排原图清晰可见 065/125"]
    changed = backfill.repaired_scanner_result({}, record, approval)
    route = changed["recognition_route"][-1]
    assert route["model"] == "visual_audit"
    assert route["source"] == "source_bound_manual_visual_audit"
    with pytest.raises(ValueError, match="not bound"):
        backfill.reviewed_stamp(
            record,
            {**approval, "proof_source_sha256": "0" * 64},
        )
    with pytest.raises(ValueError, match="does not contain"):
        backfill.reviewed_stamp(
            record,
            {**approval, "visual_evidence": "背面左侧竖排原图清晰可见 066/125"},
        )


def test_approval_target_and_source_hashes_are_mandatory():
    record = fixture_record()
    for mutation in (
        {"owner_id": "another-owner"},
        {"collection_item_id": "another-item"},
        {"source_sha256": {"front": FRONT_SHA, "back": "0" * 64}},
        {"serial_number": True},
    ):
        with pytest.raises(ValueError):
            backfill.reviewed_stamp(record, {**APPROVAL, **mutation})


def test_preflight_uses_owner_and_item_not_display_id():
    first = fixture_record()
    second = deepcopy(first)
    second.update(owner_id="owner-2", collection_item_id="item-2")
    second_approval = {
        **APPROVAL,
        "owner_id": "owner-2",
        "collection_item_id": "item-2",
    }

    resolved = backfill.approved_records(
        {"items": [first, second]},
        [APPROVAL, second_approval],
    )

    assert [(record["owner_id"], record["collection_item_id"]) for record, _ in resolved] == [
        ("owner", "item"),
        ("owner-2", "item-2"),
    ]
    with pytest.raises(ValueError, match="duplicate owner/item"):
        backfill.approved_records({"items": [first]}, [APPROVAL, APPROVAL])
    with pytest.raises(ValueError, match="not present in the audit"):
        backfill.approved_records({"items": [first]}, [second_approval])


def test_repair_preserves_unrelated_fields_and_embeds_previous_evidence():
    original = {
        "serial_number": None,
        "serial_total": None,
        "is_numbered": None,
        "player_name": "Fixture",
        "card_number": "12",
        "confidence": "0.6",
        "field_evidence": {"serial_total": ["联网候选：猜测99编"], "card_number": ["No.12"]},
        "caveats": ["限编缺少图片中可见的 x/y 分数证据，已清空", "颜色未确定"],
    }
    changed = backfill.repaired_scanner_result(original, fixture_record(), APPROVAL)
    assert original["serial_total"] is None
    assert changed["serial_total"] == 249
    assert changed["card_number"] == "12"
    assert changed["confidence"] == "0.6"
    assert changed["caveats"] == ["颜色未确定"]
    assert changed["recognition_route"][-1]["before"]["field_evidence"]["serial_total"] == [
        "联网候选：猜测99编"
    ]
    assert (
        changed["recognition_route"][-1]["observations"]
        == fixture_record()["ocr"]["raw_observations"]
    )


class Connection:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.queries = []

    def transaction(self):
        return nullcontext()

    def execute(self, sql, params):
        self.queries.append(sql)
        return SimpleNamespace(fetchone=lambda: next(self.answers))


def item_row():
    return {
        "id": "item",
        "serial_number": None,
        "serial_total": None,
        "is_numbered": False,
        "print_run": None,
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"serial_total": 99}, "serial_already_set"),
        ({"print_run": 99}, "serial_already_set"),
        ({"updated_at": datetime(2026, 2, 1, tzinfo=UTC)}, "item_changed_after_audit"),
    ],
)
def test_changed_or_existing_user_values_are_not_overwritten(mutation, reason):
    connection = Connection([{**item_row(), **mutation}])
    result = backfill.apply_one(
        connection, SimpleNamespace(), fixture_record(), APPROVAL, apply=True
    )
    assert result["reason"] == reason
    assert not any(query.lstrip().startswith("UPDATE ") for query in connection.queries)


def test_newly_confirmed_identity_blocks_write_under_lock():
    connection = Connection([item_row(), {"imported_at": datetime.now(UTC)}, {"protected": True}])
    result = backfill.apply_one(
        connection, SimpleNamespace(), fixture_record(), APPROVAL, apply=True
    )
    assert result["reason"] == "protected_identity_history"
    assert not any(query.lstrip().startswith("UPDATE ") for query in connection.queries)


def test_changed_source_hash_blocks_write(tmp_path):
    path = tmp_path / "original.jpg"
    path.write_bytes(b"changed pixels")
    record = fixture_record()
    audited_hash = hashlib.sha256(b"old pixels").hexdigest()
    record["image_back_url"] = "/uploads/back.jpg"
    record["image_front_url"] = "/uploads/front.jpg"
    record["source_images"] = [
        {"side": side, "relative_path": "original.jpg", "sha256": audited_hash}
        for side in ("front", "back")
    ]
    record["ocr"]["accepted_observations"][0]["source_sha256"] = audited_hash
    approval = {
        **APPROVAL,
        "source_sha256": {"front": audited_hash, "back": audited_hash},
    }
    connection = Connection(
        [
            {
                **item_row(),
                "image_back_url": "/uploads/back.jpg",
                "image_front_url": "/uploads/front.jpg",
            },
            {
                "imported_at": datetime.now(UTC),
                "back_original_image_path": "original.jpg",
                "front_original_image_path": "original.jpg",
            },
            {"protected": False},
        ]
    )
    result = backfill.apply_one(
        connection,
        SimpleNamespace(scanner_job_dir=str(tmp_path)),
        record,
        approval,
        apply=True,
    )
    assert result["reason"] == "source_hash_mismatch"
    assert not any(query.lstrip().startswith("UPDATE ") for query in connection.queries)
