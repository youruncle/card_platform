from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from card_platform.recognition_benchmark import (
    audit_prediction_against_source_title,
    build_cardhobby_search_params,
    calculate_benchmark_metrics,
    calculate_feedback_report,
    calculate_version_metrics,
    choose_candidates,
    identity_matches_truth,
    parse_cardhobby_candidate,
    selected_candidate_identity,
    truth_is_scorable,
)
from card_platform.recognition_benchmark_predictor import (
    BENCHMARK_REMOTE_MAX_BYTES,
    RecognitionBenchmarkPredictor,
    build_blind_prediction,
    build_parallel_option_candidates,
    canonicalize_identity,
    exact_identity_confidence,
    merge_side_predictions,
    merge_serial_detail,
    make_serial_detail_crop,
    make_serial_detail_crops,
    needs_back_anchor,
    prepare_vision_image,
    select_front_back_results,
    usable_back_anchor,
)
import card_platform.recognition_benchmark_predictor as predictor_module
from card_platform.schemas import CardRecognitionResult, CardWebCandidate


class EmptyAsyncResult:
    async def fetchall(self) -> list:
        return []


class RecordingAsyncConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, query: str, params: tuple) -> EmptyAsyncResult:
        self.calls.append((query, params))
        return EmptyAsyncResult()


def card(item_id: int, *, seller: int = 1, title: str | None = None) -> dict:
    return {
        "ID": item_id,
        "Title": title or f"2023-24 Panini Prizm Player {item_id}",
        "TitImg": f"https://images.cardhobby.com/{item_id}.jpg",
        "LowestPrice": "25.00",
        "EffectiveDate": "2026-07-29 10:00:00",
        "SellMemberID": seller,
    }


def test_search_is_sold_basketball_single_card_above_twenty() -> None:
    params = build_cardhobby_search_params(3)
    assert params["pageIndex"] == 3
    assert '"Status","Value":-2' in params["searchJson"]
    assert '"Sports","Value":"Basketball"' in params["searchJson"]
    assert '"MinPrice","Value":"20"' in params["searchJson"]
    assert "10106fda-c5d8-4121-ab21-0428534eaf42" in params["searchJson"]


@pytest.mark.asyncio
async def test_segmented_replay_skips_samples_already_on_current_pipeline() -> None:
    predictor = object.__new__(RecognitionBenchmarkPredictor)
    connection = RecordingAsyncConnection()

    result = await predictor.predict_batch(
        connection,
        "batch-7",
        limit=50,
        retry_errors=False,
        replay_all=True,
        knowledge_version=None,
    )

    query, params = connection.calls[0]
    assert "prediction_pipeline_version IS DISTINCT FROM %s" in query
    assert params == (
        "batch-7",
        predictor_module.RECOGNITION_PIPELINE_VERSION,
        50,
    )
    assert result == {"requested": 0, "predicted": 0, "failed": 0}


def test_candidate_uses_final_price_and_rejects_lots() -> None:
    candidate = parse_cardhobby_candidate(card(10))
    assert candidate is not None
    assert candidate.final_price == Decimal("25.00")
    assert candidate.occurred_at == datetime(2026, 7, 29, 10, 0)
    assert parse_cardhobby_candidate(card(11, title="篮球卡 LOT 打包")) is None


@pytest.mark.parametrize(
    "title",
    [
        "2025-26 Topps Hoops RC 银折 共3张",
        "2026 Panini Prizm Phil Foden 曼城 签字",
        "FIFA World Cup Prizm football card",
    ],
)
def test_candidate_rejects_multi_card_and_football_titles(title: str) -> None:
    assert parse_cardhobby_candidate(card(12, title=title)) is None


def test_candidate_selection_caps_sellers_and_deduplicates_titles() -> None:
    candidates = [
        parse_cardhobby_candidate(card(index, seller=1 if index < 5 else index))
        for index in range(1, 10)
    ]
    selected = choose_candidates(
        [candidate for candidate in candidates if candidate],
        sample_size=6,
        max_per_seller=2,
        seed=7,
    )
    assert len(selected) == 6
    assert sum(candidate.seller_id == "1" for candidate in selected) <= 2


@pytest.mark.asyncio
async def test_image_download_skips_one_broken_photo_when_another_is_usable(
    monkeypatch,
) -> None:
    success, encoded = cv2.imencode(
        ".jpg",
        np.full((480, 320, 3), 180, dtype=np.uint8),
    )
    assert success

    class Response:
        def __init__(self, content: bytes):
            self.content = content

        def raise_for_status(self) -> None:
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str):
            return Response(
                b"<html>temporary CDN response</html>"
                if "broken" in url
                else encoded.tobytes()
            )

    monkeypatch.setattr(
        predictor_module.httpx,
        "AsyncClient",
        lambda **_kwargs: Client(),
    )
    predictor = RecognitionBenchmarkPredictor.__new__(
        RecognitionBenchmarkPredictor
    )
    predictor.settings = SimpleNamespace(
        provider_timeout_seconds=1,
        max_image_bytes=1024 * 1024,
    )
    images = await predictor._download_images(
        [
            "https://images.cardhobby.com/broken.jpg",
            "https://images.cardhobby.com/usable.jpg",
        ]
    )
    assert len(images) == 1
    assert images[0][1] == "image/jpeg"


def test_metrics_exclude_insufficient_and_source_errors() -> None:
    metrics = calculate_benchmark_metrics(
        [
            {"label_result": "top1_correct"},
            {"label_result": "top2_correct"},
            {"label_result": "none_correct"},
            {"label_result": "insufficient_image"},
            {"label_result": "source_error"},
            {"label_result": None},
        ]
    )
    assert metrics["scored_count"] == 3
    assert metrics["top1_accuracy"] == 0.3333
    assert metrics["top3_recall"] == 0.6667
    assert metrics["insufficient_image_count"] == 1


def test_selected_candidate_identity_preserves_human_rank_truth() -> None:
    row = {
        "label_result": "top2_correct",
        "prediction": {
            "candidates": [
                {"rank": 1, "identity": {"player_name": "Wrong Player"}},
                {
                    "rank": 2,
                    "identity": {
                        "player_name": "Stephen Curry",
                        "is_autograph": False,
                    },
                },
            ]
        },
    }

    assert selected_candidate_identity(row) == {
        "player_name": "Stephen Curry",
        "is_autograph": False,
    }


def test_identity_matches_truth_uses_all_known_structured_fields() -> None:
    truth = {
        "player_name": "Nikola Jokić",
        "product_name": "Panini Obsidian Basketball",
        "is_autograph": False,
        "memorabilia_types": ["patch", "jersey"],
    }
    assert identity_matches_truth(
        {
            "player_name": "Nikola Jokic",
            "product_name": "Obsidian",
            "is_autograph": False,
            "memorabilia_types": ["jersey", "patch"],
        },
        truth,
    )
    assert not identity_matches_truth(
        {
            "player_name": "Nikola Jokic",
            "product_name": "Obsidian",
            "is_autograph": True,
            "memorabilia_types": ["patch", "jersey"],
        },
        truth,
    )


def test_exact_card_truth_requires_player_product_and_two_anchors() -> None:
    assert not truth_is_scorable({"player_name": "Jerry West"})
    assert not truth_is_scorable(
        {
            "player_name": "Jerry West",
            "product_name": "National Treasures",
            "season_start": 2015,
        }
    )
    assert truth_is_scorable(
        {
            "player_name": "Jerry West",
            "product_name": "National Treasures",
            "season_start": 2015,
            "serial_total": 10,
        }
    )


def test_version_metrics_reports_ranking_errors_and_attribute_accuracy() -> None:
    truth = {
        "player_name": "Stephen Curry",
        "season_start": 2024,
        "product_name": "Obsidian Basketball",
        "is_numbered": True,
        "serial_total": 1,
        "is_autograph": False,
    }
    rows = [
        {
            "sample_id": "sample-1",
            "pipeline_version": "image-v4.0",
            "prediction": {
                "candidates": [
                    {
                        "identity": {
                            **truth,
                            "is_autograph": True,
                        }
                    },
                    {"identity": truth},
                ]
            },
            "prediction_error": None,
            "corrected_identity": truth,
            "label_result": "top2_correct",
        },
        {
            "sample_id": "sample-1",
            "pipeline_version": "image-v4.1.1",
            "prediction": {"candidates": [{"identity": truth}]},
            "prediction_error": None,
            "corrected_identity": truth,
            "label_result": "top2_correct",
        },
        {
            "sample_id": "sample-2",
            "pipeline_version": "image-v4.1.1",
            "prediction": None,
            "prediction_error": "download failed",
            "corrected_identity": {
                "player_name": "Jerry West",
                "product_name": "National Treasures",
                "season_start": 2015,
                "serial_total": 10,
            },
            "label_result": "none_correct",
        },
        {
            "sample_id": "sample-3",
            "pipeline_version": "image-v4.1.1",
            "prediction": {"candidates": []},
            "prediction_error": None,
            "corrected_identity": None,
            "label_result": "none_correct",
        },
    ]

    report = calculate_version_metrics(rows)
    assert report["ground_truth_count"] == 2
    assert report["scorable_ground_truth_count"] == 2
    assert report["incomplete_ground_truth_count"] == 0
    assert report["unresolved_labeled_count"] == 1
    v40, v411 = report["versions"]
    assert v40["top1_accuracy"] == 0
    assert v40["top3_recall"] == 1
    assert v40["candidate_count_distribution"] == {
        "0": 0,
        "1": 0,
        "2": 1,
        "3": 0,
    }
    assert v40["multi_candidate_rate"] == 1
    autograph = next(
        item for item in v40["field_metrics"] if item["field"] == "is_autograph"
    )
    assert autograph == {
        "field": "is_autograph",
        "correct": 0,
        "total": 1,
        "accuracy": 0,
    }
    assert v411["sample_count"] == 2
    assert v411["history_sample_count"] == 3
    assert v411["prediction_error_count"] == 2
    assert v411["candidate_count_distribution"] == {
        "0": 2,
        "1": 1,
        "2": 0,
        "3": 0,
    }
    assert v411["three_candidate_rate"] == 0
    assert v411["top1_accuracy"] == 0.5
    assert v411["top3_recall"] == 0.5
    assert v411["acceptance_evaluable"] is False
    assert v411["acceptance_status"] == "pending"


def test_version_metrics_requires_full_50_sample_acceptance_gate() -> None:
    rows = []
    for index in range(50):
        truth = {
            "player_name": f"Player {index}",
            "product_name": "Prizm",
            "season_start": 2024,
            "card_number": str(index),
        }
        candidates = []
        if index < 45:
            candidates = [{"identity": truth}]
        elif index < 49:
            candidates = [
                {"identity": {**truth, "card_number": f"wrong-{index}"}},
                {"identity": truth},
            ]
        else:
            candidates = [{"identity": {**truth, "product_name": "Select"}}]
        rows.append(
            {
                "sample_id": f"sample-{index}",
                "pipeline_version": "image-v-test",
                "prediction": {"candidates": candidates},
                "prediction_error": None,
                "corrected_identity": truth,
                "label_result": "top1_correct" if index < 45 else "top2_correct",
            }
        )

    pending = calculate_version_metrics(rows[:49])["versions"][0]
    assert pending["top1_accuracy"] > 0.9
    assert pending["top3_recall"] == 1
    assert pending["acceptance_status"] == "pending"

    complete = calculate_version_metrics(rows)["versions"][0]
    assert complete["top1_accuracy"] == 0.9
    assert complete["top3_recall"] == 0.98
    assert complete["acceptance_evaluable"] is True
    assert complete["acceptance_status"] == "passed"


def test_version_metrics_separates_same_pipeline_by_knowledge_version() -> None:
    truth = {
        "player_name": "Luka Doncic",
        "season_start": 2020,
        "product_name": "NBA Hoops",
        "insert_name": "SLAM",
    }
    rows = [
        {
            "sample_id": "sample-1",
            "pipeline_version": "image-v4.1.1",
            "knowledge_version": "knowledge-230",
            "knowledge_version_number": 230,
            "model": "vision-model",
            "prediction": {
                "candidates": [
                    {"identity": {**truth, "insert_name": "Arriving Now"}}
                ]
            },
            "prediction_error": None,
            "corrected_identity": truth,
            "label_result": "none_correct",
        },
        {
            "sample_id": "sample-1",
            "pipeline_version": "image-v4.1.1",
            "knowledge_version": "knowledge-231",
            "knowledge_version_number": 231,
            "model": "vision-model",
            "prediction": {"candidates": [{"identity": truth}]},
            "prediction_error": None,
            "corrected_identity": truth,
            "label_result": "top1_correct",
        },
    ]

    report = calculate_version_metrics(rows)
    assert len(report["versions"]) == 2
    by_knowledge = {
        version["knowledge_version_number"]: version
        for version in report["versions"]
    }
    assert by_knowledge[230]["top1_accuracy"] == 0
    assert by_knowledge[231]["top1_accuracy"] == 1


def test_feedback_report_prioritizes_corrected_field_errors() -> None:
    report = calculate_feedback_report(
        [
            {
                "label_result": "none_correct",
                "prediction": {
                    "candidates": [
                        {
                            "identity": {
                                "player_name": "Nikola Jokic",
                                "product_name": "Prizm",
                            }
                        }
                    ]
                },
                "corrected_identity": {
                    "player_name": "Nikola Jokić",
                    "product_name": "Select",
                    "card_number": "7",
                },
            },
            {
                "label_result": "top1_correct",
                "prediction": {"candidates": []},
                "corrected_identity": None,
            },
        ]
    )
    assert report["label_counts"] == {"none_correct": 1, "top1_correct": 1}
    assert report["knowledge_queue_count"] == 1
    errors = {item["field"]: item["errors"] for item in report["field_errors"]}
    assert errors == {"player_name": 1, "product_name": 1, "card_number": 1}


def test_feedback_report_learns_from_selected_candidate_rank() -> None:
    report = calculate_feedback_report(
        [
            {
                "label_result": "top3_correct",
                "prediction": {
                    "candidates": [
                        {"rank": 1, "identity": {"product_name": "Prizm"}},
                        {"rank": 2, "identity": {"product_name": "Select"}},
                        {
                            "rank": 3,
                            "identity": {
                                "product_name": "National Treasures",
                                "card_number": "LL-KP",
                            },
                        },
                    ]
                },
                "corrected_identity": None,
            }
        ]
    )
    assert report["corrected_sample_count"] == 1
    assert report["knowledge_queue_count"] == 1
    errors = {item["field"]: item["errors"] for item in report["field_errors"]}
    assert errors == {"product_name": 1, "card_number": 1}


def test_recognition_attributes_are_structured_and_coherent() -> None:
    result = CardRecognitionResult(
        player_name="Example Player",
        serial_number=3,
        serial_total=10,
        is_rookie=True,
        rookie_evidence=["RC logo on front"],
        is_autograph=True,
        autograph_type="sticker",
        is_memorabilia=True,
        memorabilia_types=["jersey"],
        memorabilia_usage="player_worn",
        memorabilia_player="Example Player",
        confidence=Decimal("0.9"),
    )
    assert result.is_numbered is True
    assert result.autograph_type == "sticker"
    assert result.memorabilia_usage == "player_worn"
    prediction = build_blind_prediction(result)
    identity = prediction["candidates"][0]["identity"]
    assert identity["is_numbered"] is True
    assert identity["rookie_evidence"] == ["RC logo on front"]
    assert identity["autograph_type"] == "sticker"
    assert identity["memorabilia_types"] == ["jersey"]
    assert identity["memorabilia_usage"] == "player_worn"


def test_unsupported_negative_card_attributes_become_unknown() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Example Player",
            is_rookie=False,
            is_autograph=False,
            is_memorabilia=False,
            is_numbered=False,
            confidence=Decimal("0.8"),
            field_evidence={},
        )
    )
    assert normalized.is_rookie is None
    assert normalized.is_autograph is None
    assert normalized.autograph_type is None
    assert normalized.is_memorabilia is None
    assert normalized.memorabilia_usage is None
    assert normalized.is_numbered is None


def test_unordered_images_select_actual_front_and_back() -> None:
    duplicate_front = CardRecognitionResult(
        image_role="front",
        player_name="Jerry West",
        is_autograph=True,
        confidence=Decimal("0.8"),
        field_evidence={"player_name": ["front name"], "is_autograph": ["visible ink"]},
    )
    advertisement = CardRecognitionResult(
        image_role="ad",
        confidence=Decimal("0.1"),
        field_evidence={},
    )
    actual_back = CardRecognitionResult(
        image_role="back",
        season_start=2015,
        season_end=2016,
        product_name="National Treasures",
        card_number="S-JWS",
        confidence=Decimal("0.9"),
        field_evidence={
            "season_start": ["back product line"],
            "product_name": ["back product line"],
            "card_number": ["back No. S-JWS"],
        },
    )
    front, back = select_front_back_results(
        [duplicate_front, advertisement, actual_back]
    )
    assert front is duplicate_front
    assert back is actual_back


def test_product_line_evidence_deterministically_sets_season() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            image_role="back",
            season_start=2016,
            product_name="National Treasures Basketball",
            confidence=Decimal("0.9"),
            field_evidence={
                "product_name": [
                    "背面：2015-16 PANINI-NATIONAL TREASURES BASKETBALL"
                ]
            },
        )
    )
    assert normalized.season_start == 2015
    assert normalized.season_end == 2016


def test_product_line_season_wins_over_polluted_career_evidence() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            image_role="back",
            player_name="JERRY WEST",
            season_start=2016,
            copyright_year=2016,
            product_name="National Treasures",
            confidence=0.9,
            field_evidence={
                "product_name": [
                    "背面：2015-16 PANINI-NATIONAL TREASURES BASKETBALL"
                ],
                "season_start": [
                    "Jerry West rookie season and career history were considered"
                ],
            },
        )
    )

    assert normalized.season_start == 2015
    assert normalized.season_end == 2016


def test_explicit_season_field_evidence_sets_release_season() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="JERRY WEST",
            season_start=2016,
            copyright_year=2016,
            product_name="National Treasures",
            confidence=0.9,
            field_evidence={
                "product_name": ["PANINI—NATIONAL TREASURES BASKETBALL"],
                "season_start": ["背面：2015-16"],
            },
        )
    )

    assert normalized.season_start == 2015
    assert normalized.season_end == 2016


def test_inferred_product_season_is_not_treated_as_printed_release_evidence() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Dwyane Wade",
            season_start=2012,
            season_end=2013,
            product_name="Noir",
            confidence=Decimal("0.9"),
            field_evidence={
                "season_start": [
                    "结合 NOIR 系列为 2013 年 Panini 发行，对应 2012-13 赛季"
                ],
                "season_end": ["同上，赛季跨度为 2012-13"],
            },
            caveats=["赛季只能根据正面风格推断"],
        )
    )
    assert normalized.season_start is None
    assert normalized.season_end is None


def test_verified_catalog_season_is_retained() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Dwyane Wade",
            season_start=2020,
            season_end=2021,
            product_name="Noir",
            confidence=Decimal("0.9"),
            field_evidence={
                "season_start": ["目录核验：官方 checklist 明确为 2020-21"],
            },
        )
    )
    assert normalized.season_start == 2020
    assert normalized.season_end == 2021


def test_explicit_release_season_wins_over_unresolved_caveat() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="JERRY WEST",
            product_name="National Treasures",
            confidence=0.8,
            caveats=["赛季字段仍未解决"],
            field_evidence={"season_start": ["2015-16"]},
        )
    )

    assert normalized.season_start == 2015
    assert normalized.season_end == 2016


def test_labeled_alphanumeric_card_number_is_not_an_insert() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="JERRY WEST",
            insert_name="S-JWS",
            confidence=0.8,
            field_evidence={"card_number": ["卡背顶部 Nº S-JWS"]},
        )
    )

    assert normalized.card_number == "S-JWS"
    assert normalized.insert_name is None


def test_visible_numbered_evidence_wins_over_card_number_fraction_confusion() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="NOLAN TRAORE",
            season_start=2025,
            season_end=2026,
            brand_name="Topps",
            product_name="Topps Chrome",
            card_number="269",
            serial_number=269,
            serial_total=269,
            parallel_name="Gold",
            confidence=0.9,
            field_evidence={
                "is_numbered": ["49/50 printed at top-right", "背面：269"],
            },
        )
    )

    assert normalized.is_numbered is True
    assert normalized.serial_number == 49
    assert normalized.serial_total == 50
    assert normalized.parallel_name == "Gold"


def test_nolan_gold_chrome_alias_normalizes_to_gold_taxonomy() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="NOLAN TRAORE",
            brand_name="Topps",
            product_name="Topps Chrome",
            card_number="269",
            serial_number=49,
            serial_total=50,
            is_numbered=True,
            parallel_name="Gold Chrome",
            confidence=0.9,
            field_evidence={"is_numbered": ["49/50"]},
        )
    )

    assert normalized.parallel_name == "Gold"


def test_card_number_repeated_as_serial_is_cleared_without_fraction_evidence() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            card_number="269",
            serial_number=269,
            serial_total=269,
            is_numbered=True,
            confidence=0.8,
            field_evidence={"is_numbered": ["OCR: 269"]},
        )
    )

    assert normalized.is_numbered is None
    assert normalized.serial_number is None
    assert normalized.serial_total is None


def test_partial_serial_without_visible_denominator_is_cleared() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Stephen Curry",
            card_number="2",
            serial_number=2,
            serial_total=None,
            is_numbered=True,
            confidence=Decimal("0.9"),
            field_evidence={"is_numbered": ["背面仅标 No. 2，未见 /XX"]},
        )
    )
    assert normalized.card_number == "2"
    assert normalized.is_numbered is None
    assert normalized.serial_number is None
    assert normalized.serial_total is None


@pytest.mark.parametrize(
    ("serial_number", "serial_total", "evidence"),
    [
        (10, 22, "No printed serial visible; 10/22/25 is the NBA debut date."),
        (3, 11, "联网候选：Born 3/11/79 was interpreted as a limited edition"),
        (5, 24, "联网候选：5/24 编码格式为典型限编标识"),
        (8, 23, "手写套袋标记 8/23，并非卡面印刷限编"),
    ],
)
def test_dates_remote_claims_and_sleeve_notes_cannot_create_serials(
    serial_number: int,
    serial_total: int,
    evidence: str,
) -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            serial_number=serial_number,
            serial_total=serial_total,
            is_numbered=True,
            confidence=Decimal("0.9"),
            field_evidence={"is_numbered": [evidence]},
        )
    )

    assert normalized.is_numbered is None
    assert normalized.serial_number is None
    assert normalized.serial_total is None


def test_clear_physical_fraction_remains_valid_serial_evidence() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            serial_number=37,
            serial_total=100,
            is_numbered=True,
            confidence=Decimal("0.9"),
            field_evidence={"is_numbered": ["正面左侧竖排 37/100 清晰可见"]},
        )
    )

    assert normalized.is_numbered is True
    assert normalized.serial_number == 37
    assert normalized.serial_total == 100


def test_serial_evidence_on_numeric_fields_is_not_discarded() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            serial_number=20,
            serial_total=20,
            is_numbered=True,
            confidence=Decimal("0.9"),
            field_evidence={
                "serial_number": ["Apple Vision OCR：20/20"],
                "serial_total": ["Apple Vision OCR：20/20"],
            },
        )
    )

    assert normalized.is_numbered is True
    assert normalized.serial_number == 20
    assert normalized.serial_total == 20


def test_prefixed_card_number_cannot_be_invented_as_serial_fraction() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Victor Wembanyama",
            card_number="VW-6",
            serial_number=5,
            serial_total=6,
            is_numbered=True,
            confidence=Decimal("0.9"),
            field_evidence={
                "card_number": ["卡背右上角可见 VW-6"],
                "is_numbered": ["根据 VW-6 推测为 5/6"],
            },
        )
    )

    assert normalized.card_number == "VW-6"
    assert normalized.is_numbered is None
    assert normalized.serial_number is None
    assert normalized.serial_total is None


def test_parallel_name_cannot_duplicate_card_number() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Khaman Maluach",
            card_number="IS-KM",
            parallel_name="IS-KM",
            confidence=Decimal("0.9"),
        )
    )
    assert normalized.card_number == "IS-KM"
    assert normalized.parallel_name is None


def test_college_record_year_does_not_override_top1_without_release_proof() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Dylan Harper",
            brand_name="Topps",
            product_name="Topps Chrome",
            season_start=2024,
            season_end=2025,
            copyright_year=2025,
            confidence=Decimal("0.9"),
            field_evidence={
                "season_start": ["背面 YEAR SCHOOL: 24-25 RUTGERS"],
                "copyright_year": ["© 2025 THE TOPPS COMPANY, INC."],
            },
        )
    )

    assert normalized.season_start == 2024
    assert normalized.season_end == 2025


def test_product_finish_aliases_normalize_parallel_name() -> None:
    prizm = canonicalize_identity(
        CardRecognitionResult(
            product_name="Prizm Basketball",
            parallel_name="Prizm",
            confidence=Decimal("0.8"),
        )
    )
    chrome = canonicalize_identity(
        CardRecognitionResult(
            brand_name="Topps",
            product_name="Topps Chrome",
            parallel_name="Refractor",
            confidence=Decimal("0.8"),
        )
    )
    chrome_logo = canonicalize_identity(
        CardRecognitionResult(
            product_name="Topps Chrome Cosmic",
            parallel_name="Chrome",
            confidence=Decimal("0.8"),
        )
    )

    assert prizm.parallel_name is None
    assert chrome.parallel_name == "Silver"
    assert chrome_logo.parallel_name == "Chrome"


def test_generic_product_logo_cannot_pollute_parallel_name() -> None:
    product_logo = canonicalize_identity(
        CardRecognitionResult(
            product_name="Obsidian Basketball",
            parallel_name="Obsidian",
            confidence=Decimal("0.8"),
        )
    )
    assert product_logo.parallel_name is None
    noir_logo = canonicalize_identity(
        CardRecognitionResult(
            product_name="Noir Basketball",
            parallel_name="NOIR",
            confidence=Decimal("0.8"),
        )
    )
    assert noir_logo.parallel_name is None


def test_product_name_aliases_use_stable_benchmark_taxonomy() -> None:
    prizm = canonicalize_identity(
        CardRecognitionResult(
            product_name="Prizm Basketball",
            confidence=Decimal("0.8"),
        )
    )
    inception = canonicalize_identity(
        CardRecognitionResult(
            brand_name="Topps",
            product_name="Inception",
            confidence=Decimal("0.8"),
        )
    )
    court_kings = canonicalize_identity(
        CardRecognitionResult(
            brand_name="Panini",
            product_name="Court Kings Basketball",
            confidence=Decimal("0.8"),
        )
    )
    mosaic = canonicalize_identity(
        CardRecognitionResult(
            brand_name="Panini",
            product_name="Mosaic Basketball",
            confidence=Decimal("0.8"),
        )
    )

    assert prizm.product_name == "Prizm"
    assert inception.product_name == "Topps Inception"
    assert court_kings.product_name == "Court Kings"
    assert mosaic.product_name == "Mosaic"


def test_rookie_rc_card_is_an_attribute_not_an_insert_name() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            insert_name="Rookie RC Card",
            is_rookie=True,
            rookie_evidence=["RC logo on front"],
            confidence=Decimal("0.8"),
        )
    )

    assert normalized.insert_name is None
    assert normalized.is_rookie is True


def test_autograph_prefix_is_not_part_of_color_parallel() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            product_name="Prizm",
            parallel_name="Auto-Red",
            is_autograph=True,
            confidence=Decimal("0.8"),
        )
    )

    assert normalized.parallel_name == "Red"


def test_compound_parallel_finish_normalizes_to_color_taxonomy() -> None:
    sky_blue = canonicalize_identity(
        CardRecognitionResult(
            brand_name="Topps",
            product_name="Finest",
            parallel_name="Sky Blue Refractor",
            confidence=Decimal("0.8"),
        )
    )
    gold = canonicalize_identity(
        CardRecognitionResult(
            brand_name="Topps",
            product_name="Topps Chrome",
            parallel_name="Gold/Prizm",
            confidence=Decimal("0.8"),
        )
    )

    assert sky_blue.parallel_name == "Blue"
    assert gold.parallel_name == "Gold"

    black = canonicalize_identity(
        CardRecognitionResult(
            product_name="Prizm",
            parallel_name="PRIZM BLACK",
            confidence=Decimal("0.8"),
        )
    )
    disco = canonicalize_identity(
        CardRecognitionResult(
            product_name="Prizm",
            parallel_name="Green Disco",
            confidence=Decimal("0.8"),
        )
    )
    assert black.parallel_name == "Black"
    assert disco.parallel_name == "Green"


def test_serial_detail_crop_preserves_upper_right_number() -> None:
    image = np.zeros((1000, 700, 3), dtype=np.uint8)
    cv2.putText(
        image,
        "49/50",
        (480, 150),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.2,
        (255, 255, 255),
        3,
    )
    success, encoded = cv2.imencode(".jpg", image)
    assert success

    crop = make_serial_detail_crop((encoded.tobytes(), "image/jpeg"))

    assert crop is not None
    cropped_image = cv2.imdecode(
        np.frombuffer(crop[0], dtype=np.uint8), cv2.IMREAD_COLOR
    )
    assert cropped_image is not None
    assert cropped_image.shape[1] >= 700
    assert int(cropped_image.max()) > 200


def test_serial_detail_crops_include_lower_left_fraction() -> None:
    image = np.zeros((1000, 700, 3), dtype=np.uint8)
    cv2.putText(
        image,
        "06/35",
        (40, 900),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.4,
        (255, 255, 255),
        3,
    )
    success, encoded = cv2.imencode(".jpg", image)
    assert success
    crops = make_serial_detail_crops((encoded.tobytes(), "image/jpeg"))
    assert len(crops) == 4
    lower_left = cv2.imdecode(
        np.frombuffer(crops[1][0], dtype=np.uint8), cv2.IMREAD_COLOR
    )
    assert lower_left is not None
    assert int(lower_left.max()) > 200


def test_serial_detail_crops_include_lower_right_fraction() -> None:
    image = np.zeros((1000, 700, 3), dtype=np.uint8)
    cv2.putText(
        image,
        "17/25",
        (460, 900),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.4,
        (255, 255, 255),
        3,
    )
    success, encoded = cv2.imencode(".jpg", image)
    assert success
    crops = make_serial_detail_crops((encoded.tobytes(), "image/jpeg"))
    lower_right = cv2.imdecode(
        np.frombuffer(crops[2][0], dtype=np.uint8), cv2.IMREAD_COLOR
    )
    assert lower_right is not None
    assert int(lower_right.max()) > 200


def test_serial_detail_crops_include_left_middle_fraction() -> None:
    image = np.zeros((1000, 700, 3), dtype=np.uint8)
    cv2.putText(
        image,
        "20/20",
        (40, 500),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.4,
        (255, 255, 255),
        3,
    )
    success, encoded = cv2.imencode(".jpg", image)
    assert success

    crops = make_serial_detail_crops((encoded.tobytes(), "image/jpeg"))
    upper_left = cv2.imdecode(
        np.frombuffer(crops[3][0], dtype=np.uint8), cv2.IMREAD_COLOR
    )

    assert len(crops) == 4
    assert upper_left is not None
    assert int(upper_left.max()) > 200


def test_serial_detail_reader_can_only_override_supported_serial_fields() -> None:
    base = CardRecognitionResult(
        player_name="NOLAN TRAORE",
        card_number="269",
        is_memorabilia=False,
        confidence=0.8,
    )
    detail = CardRecognitionResult(
        player_name="WRONG PLAYER",
        is_numbered=True,
        serial_number=49,
        serial_total=50,
        is_memorabilia=True,
        memorabilia_types=["patch"],
        confidence=0.9,
        field_evidence={"is_numbered": ["清晰可见 49/50"]},
    )

    merged = merge_serial_detail(base, detail)

    assert merged.player_name == "NOLAN TRAORE"
    assert merged.card_number == "269"
    assert merged.is_numbered is True
    assert merged.serial_number == 49
    assert merged.serial_total == 50
    assert merged.is_memorabilia is False


def test_serial_detail_accepts_evidence_under_numeric_fields() -> None:
    base = CardRecognitionResult(player_name="JASON CROWE JR.", confidence=0.8)
    detail = CardRecognitionResult(
        is_numbered=True,
        serial_number=20,
        serial_total=20,
        confidence=0.9,
        field_evidence={"serial_total": ["背面左侧竖排 20／20 清晰可见"]},
    )

    merged = merge_serial_detail(base, detail)

    assert (merged.serial_number, merged.serial_total) == (20, 20)
    assert "20／20" in merged.field_evidence["is_numbered"][-1]


def test_serial_detail_cannot_override_existing_visible_fraction() -> None:
    base = CardRecognitionResult(
        player_name="James Harden",
        is_numbered=True,
        serial_number=6,
        serial_total=35,
        confidence=Decimal("0.9"),
        field_evidence={"is_numbered": ["正面清晰可见 06/35"]},
    )
    mistaken_logo_detail = CardRecognitionResult(
        is_numbered=True,
        serial_number=1,
        serial_total=1,
        confidence=Decimal("0.8"),
        field_evidence={"is_numbered": ["误将 One and One 商标读成 1/1"]},
    )
    merged = merge_serial_detail(base, mistaken_logo_detail)
    assert merged.serial_number == 6
    assert merged.serial_total == 35


def test_autograph_inscription_is_structured_and_kept_in_blind_identity() -> None:
    result = CardRecognitionResult(
        player_name="JERRY WEST",
        is_autograph=True,
        autograph_type="on_card",
        has_inscription=True,
        inscription_text="14X NBA ALL-STAR",
        confidence=0.9,
    )

    prediction = build_blind_prediction(result)
    identity = prediction["candidates"][0]["identity"]

    assert result.is_autograph is True
    assert result.has_inscription is True
    assert identity["has_inscription"] is True
    assert identity["inscription_text"] == "14X NBA ALL-STAR"


def test_inscription_text_implies_autograph() -> None:
    result = CardRecognitionResult(
        is_autograph=False,
        inscription_text="HOF 1980",
        confidence=0.8,
    )

    assert result.is_autograph is True
    assert result.has_inscription is True


def test_printed_team_logo_is_not_accepted_as_memorabilia_patch() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Jerry West",
            is_memorabilia=True,
            memorabilia_types=["patch"],
            memorabilia_usage="player_worn",
            memorabilia_player="Jerry West",
            is_patch=True,
            confidence=Decimal("0.9"),
            field_evidence={
                "is_memorabilia": ["背面紫色方框内为湖人队徽布料 patch"],
                "memorabilia_usage": ["结合历史卡品惯例，该 patch 为球员亲穿"],
            },
        )
    )
    assert normalized.is_memorabilia is None
    assert normalized.is_patch is None
    assert normalized.memorabilia_types == []
    assert normalized.memorabilia_usage is None
    assert normalized.memorabilia_player is None


@pytest.mark.parametrize(
    "evidence",
    [
        (
            "背面中央紫色方块含湖人队徽，材质纹理符合球衣布料；"
            "结合常规产品设计，该区域应为球衣补丁，常见于 player-worn"
        ),
        (
            "联网候选：front: jersey-shaped cutout; no explicit enclosed "
            "material text but shape and branding imply jersey fragment"
        ),
    ],
)
def test_inferred_memorabilia_language_cannot_create_positive(evidence: str) -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            is_memorabilia=True,
            memorabilia_types=["jersey"],
            memorabilia_usage="player_worn",
            is_patch=True,
            confidence=Decimal("0.9"),
            field_evidence={
                "is_memorabilia": [evidence],
                "memorabilia_usage": [evidence],
            },
        )
    )
    assert normalized.is_memorabilia is None
    assert normalized.memorabilia_types == []
    assert normalized.memorabilia_usage is None
    assert normalized.is_patch is None


def test_direct_enclosed_material_statement_keeps_memorabilia_positive() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            is_memorabilia=True,
            memorabilia_types=["jersey"],
            memorabilia_usage="player_worn",
            confidence=Decimal("0.9"),
            field_evidence={
                "is_memorabilia": [
                    "卡背明确写有 The enclosed player-worn material is guaranteed"
                ],
                "memorabilia_usage": [
                    "The enclosed player-worn material is guaranteed"
                ],
            },
        )
    )
    assert normalized.is_memorabilia is True
    assert normalized.memorabilia_types == ["jersey"]
    assert normalized.memorabilia_usage == "player_worn"


def test_post_prediction_title_audit_flags_explicit_serial_and_color_conflicts() -> None:
    audit = audit_prediction_against_source_title(
        {
            "candidates": [
                {
                    "identity": {
                        "serial_total": 25,
                        "parallel_name": "Red",
                        "grade_company": "PSA",
                        "grade_value": "10",
                    }
                }
            ]
        },
        "2025 Topps Chrome Gold 金折 49/50 PSA 10",
    )
    assert audit["status"] == "review"
    assert {conflict["field"] for conflict in audit["conflicts"]} == {
        "serial_total",
        "parallel_name",
    }


def test_post_prediction_title_audit_flags_explicit_season_conflict() -> None:
    audit = audit_prediction_against_source_title(
        {
            "candidates": [
                {
                    "identity": {
                        "season_start": 2023,
                        "season_end": 2024,
                    }
                }
            ]
        },
        "2021-22 Panini Impeccable Talen Horton-Tucker",
    )
    assert audit["status"] == "review"
    assert audit["conflicts"][0]["field"] == "season"
    assert audit["conflicts"][0]["source_hint"] == "2021-2022"


def test_post_prediction_title_audit_flags_missing_explicit_color() -> None:
    audit = audit_prediction_against_source_title(
        {
            "candidates": [
                {
                    "identity": {
                        "parallel_name": "Prizm",
                    }
                }
            ]
        },
        "2020-21 Panini Optic LaMelo Ball RC 粉hyper折",
    )
    assert audit["status"] == "review"
    assert audit["conflicts"][0]["field"] == "parallel_name"
    assert "未识别" in audit["conflicts"][0]["reason"]


def test_blind_prediction_has_primary_and_at_most_three_candidates() -> None:
    result = CardRecognitionResult(
        player_name="Nikola Jokic",
        season_start=2023,
        season_end=2024,
        brand_name="Panini",
        product_name="Prizm",
        confidence=Decimal("0.91"),
        web_candidates=[
            CardWebCandidate(
                title=f"candidate {index}",
                identity={
                    "player_name": "Nikola Jokic",
                    "product_name": "Select",
                    "card_number": str(index),
                },
                source_urls=[f"https://www.tcdb.com/card/{index}"],
                match_confidence=Decimal("0.8"),
            )
            for index in range(4)
        ],
    )
    prediction = build_blind_prediction(result)
    assert len(prediction["candidates"]) == 3
    assert prediction["candidates"][0]["identity"]["player_name"] == "Nikola Jokic"
    assert prediction["candidates"][2]["rank"] == 3


def test_side_predictions_use_back_for_card_number_and_front_for_visual_fields() -> None:
    front = CardRecognitionResult(
        player_name="Nikola Jokic",
        product_name="Prizm",
        insert_name="Fireworks",
        card_number="wrong",
        confidence=Decimal("0.8"),
        field_evidence={"player_name": ["name visible"]},
    )
    back = CardRecognitionResult(
        player_name="N. Jokic",
        season_start=2023,
        season_end=2024,
        product_name="Panini Prizm",
        card_number="7",
        confidence=Decimal("0.9"),
        field_evidence={"card_number": ["back number"]},
    )
    merged = merge_side_predictions(front, back)
    assert merged.player_name == "Nikola Jokic"
    assert merged.insert_name == "Fireworks"
    assert merged.card_number == "7"
    assert merged.product_name == "Panini Prizm"
    assert merged.confidence == Decimal("0.85")
    assert merged.field_evidence["player_name"] == ["正面：name visible"]
    assert merged.field_evidence["card_number"] == ["背面：back number"]


def test_back_printed_player_identity_overrides_front_face_guess() -> None:
    front = CardRecognitionResult(
        image_role="front",
        player_name="LaMelo Ball",
        is_rookie=False,
        is_autograph=True,
        is_memorabilia=True,
        memorabilia_usage="player_worn",
        memorabilia_player="LaMelo Ball",
        confidence=Decimal("0.8"),
        field_evidence={"player_name": ["face resembles LaMelo Ball"]},
    )
    back = CardRecognitionResult(
        image_role="back",
        player_name="KJ Simpson Jr.",
        season_start=2024,
        season_end=2025,
        product_name="Noir Basketball",
        card_number="361",
        is_rookie=True,
        is_autograph=True,
        is_memorabilia=True,
        memorabilia_usage="not_from_specific_game_or_event",
        memorabilia_player=None,
        confidence=Decimal("0.95"),
        field_evidence={
            "player_name": ["back nameplate reads KJ SIMPSON JR."],
            "card_number": ["No. 361"],
        },
    )
    merged = merge_side_predictions(front, back)
    assert merged.player_name == "KJ Simpson Jr."
    assert merged.is_rookie is True
    assert merged.is_autograph is True
    assert merged.is_memorabilia is True
    assert merged.memorabilia_usage == "not_from_specific_game_or_event"
    assert merged.memorabilia_player is None
    assert "背面印刷球员名" in " ".join(merged.caveats)


def test_missing_back_claim_triggers_targeted_back_anchor() -> None:
    combined = CardRecognitionResult(
        player_name="LaMelo Ball",
        product_name="Noir",
        confidence=Decimal("0.8"),
        caveats=["卡背信息不可见，赛季依据产品线推断"],
    )
    assert needs_back_anchor(combined, 4)
    assert not needs_back_anchor(combined, 1)

    back = CardRecognitionResult(
        image_role="back",
        player_name="KJ Simpson Jr.",
        season_start=2024,
        season_end=2025,
        product_name="Noir Basketball",
        card_number="361",
        confidence=Decimal("0.95"),
        field_evidence={"player_name": ["KJ SIMPSON JR."]},
    )
    assert usable_back_anchor(back)
    assert not usable_back_anchor(
        CardRecognitionResult(
            player_name="KJ Simpson Jr.",
            confidence=Decimal("0.7"),
        )
    )


def test_single_side_prediction_cannot_keep_high_confidence() -> None:
    back = CardRecognitionResult(
        player_name="Nolan Traore",
        card_number="269",
        confidence=Decimal("0.95"),
    )
    merged = merge_side_predictions(None, back)
    assert merged.confidence == Decimal("0.65")
    assert "正面识别失败" in merged.caveats[-1]


def test_identity_canonicalization_removes_category_errors() -> None:
    result = CardRecognitionResult(
        player_name="Norman Powell",
        product_name="Recon Basketball",
        insert_name="Recon",
        parallel_name="03/10",
        card_number="No. 25",
        confidence=Decimal("0.95"),
    )
    normalized = canonicalize_identity(result)
    assert normalized.card_number == "25"
    assert normalized.insert_name is None
    assert normalized.parallel_name is None
    assert normalized.serial_number == 3
    assert normalized.serial_total == 10
    assert normalized.confidence == Decimal("0.83")


def test_identity_canonicalization_rejects_career_years_as_card_season() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Jerry West",
            season_start=1960,
            season_end=1974,
            brand_name="National Treasures",
            product_name="Signatures",
            insert_name="National Treasures",
            confidence=Decimal("0.88"),
            caveats=[
                "Season fields (season_start/season_end) remain unresolved: "
                "card contains no year designation"
            ],
        )
    )
    assert normalized.season_start is None
    assert normalized.season_end is None
    assert normalized.brand_name == "Panini"
    assert normalized.product_name == "National Treasures"
    assert normalized.insert_name == "Signatures"
    assert normalized.confidence == Decimal("0.76")


def test_identity_canonicalization_recovers_manufacturer_from_duplicate_product() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Stephen Curry",
            brand_name="Obsidian",
            product_name="Obsidian",
            insert_name="Supernova",
            confidence=Decimal("0.9"),
        )
    )
    assert normalized.brand_name == "Panini"
    assert normalized.product_name == "Obsidian"
    assert normalized.insert_name == "Supernova"


def test_stats_year_cannot_override_copyright_release_year() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Nolan Traore",
            season_start=2024,
            season_end=2025,
            copyright_year=2025,
            brand_name="Topps",
            product_name="Chrome",
            confidence=Decimal("0.95"),
            field_evidence={
                "season_start": ["24-25 INTERNATIONAL RECORD"],
                "season_end": ["DRAFTED: 2025 and season record"],
            },
        )
    )
    assert normalized.season_start == 2025
    assert normalized.season_end is None


def test_historical_game_date_cannot_override_black_diamond_copyright_year() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="MICHAEL JORDAN",
            season_start=1996,
            season_end=1997,
            copyright_year=1999,
            brand_name="Upper Deck",
            product_name="Black Diamond",
            card_number="12",
            confidence=Decimal("0.95"),
            field_evidence={
                "season_start": ["96-97 BULLS statistics"],
                "season_end": ["GAME-HIGH 38 POINTS IN GAME 5 NBA FINALS WIN 6-11-97"],
                "copyright_year": ["©1999 Upper Deck Company"],
            },
        )
    )

    assert normalized.season_start == 1999
    assert normalized.season_end is None


def test_rookie_award_biography_cannot_override_starquest_copyright_year() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Allen Iverson",
            season_start=1996,
            season_end=1997,
            copyright_year=1998,
            brand_name="Upper Deck",
            product_name="UD Choice",
            insert_name="Starquest",
            card_number="SQ20",
            confidence=Decimal("0.95"),
            field_evidence={
                "season_start": ["biography: Rookie of the Year Award"],
                "season_end": ["biography: each of his two professional seasons"],
                "copyright_year": ["card back bottom fine print: ©1998 Upper Deck"],
            },
        )
    )

    assert normalized.season_start == 1998
    assert normalized.season_end is None


def test_finest_protective_coating_is_not_an_insert_name() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Tracy McGrady",
            season_start=1997,
            season_end=1998,
            copyright_year=1998,
            brand_name="Topps",
            product_name="Finest",
            insert_name="Protector",
            card_number="28",
            confidence=Decimal("0.95"),
            field_evidence={
                "season_start": ["NBA HIGHS / Career 1997-98 stats"],
                "season_end": ["1997-98 season statistics"],
                "insert_name": ["TOPPS FINEST PROTECTOR - PEEL AND REMOVE COATING"],
            },
        )
    )

    assert normalized.season_start == 1998
    assert normalized.season_end is None
    assert normalized.insert_name is None


def test_web_candidates_expose_structured_identity() -> None:
    result = CardRecognitionResult(
        player_name="Nikola Jokic",
        confidence=Decimal("0.8"),
        web_candidates=[
            CardWebCandidate(
                title="2023 Prizm Nikola Jokic #7",
                identity={
                    "player_name": "Nikola Jokic",
                    "season_start": 2023,
                    "product_name": "Prizm",
                    "card_number": "7",
                },
                source_urls=["https://www.tcdb.com/card/7"],
                match_confidence=Decimal("0.7"),
            )
        ],
    )
    prediction = build_blind_prediction(result)
    assert prediction["candidates"][1]["identity"]["card_number"] == "7"


def test_web_candidate_identity_normalizes_rich_card_attributes() -> None:
    candidate = CardWebCandidate(
        title="National Treasures inscribed patch autograph",
        identity={
            "player_name": "Jerry West",
            "serial_number": 3,
            "serial_total": 10,
            "inscription_text": "14X NBA ALL-STAR",
            "is_patch": True,
            "memorabilia_types": "jersey",
        },
        match_confidence=Decimal("0.8"),
    )
    identity = candidate.identity
    assert identity.is_numbered is True
    assert identity.is_autograph is True
    assert identity.autograph_type == "unknown"
    assert identity.has_inscription is True
    assert identity.is_memorabilia is True
    assert identity.memorabilia_types == ["jersey", "patch"]


def test_blind_prediction_drops_unverified_and_duplicate_candidates() -> None:
    result = CardRecognitionResult(
        player_name="Nikola Jokic",
        product_name="Prizm",
        card_number="7",
        confidence=Decimal("0.9"),
        web_candidates=[
            CardWebCandidate(
                title="same identity",
                identity={
                    "player_name": "Nikola Jokic",
                    "product_name": "Prizm",
                    "card_number": "7",
                },
                source_urls=["https://www.tcdb.com/card/7"],
                match_confidence=Decimal("0.9"),
            ),
            CardWebCandidate(
                title="unverified alternative",
                identity={"player_name": "Nikola Jokic", "card_number": "8"},
                match_confidence=Decimal("0.8"),
            ),
        ],
    )
    prediction = build_blind_prediction(result)
    assert len(prediction["candidates"]) == 2
    assert prediction["candidates"][1]["source_type"] == "structured_uncertainty"


def test_blind_prediction_keeps_concrete_unverified_web_hypothesis() -> None:
    result = CardRecognitionResult(
        player_name="Luka Doncic",
        confidence=Decimal("0.8"),
        web_candidates=[
            CardWebCandidate(
                title="2020-21 Hoops SLAM #5 Luka Doncic",
                identity={
                    "player_name": "Luka Doncic",
                    "season_start": 2020,
                    "product_name": "Hoops",
                    "insert_name": "SLAM",
                    "card_number": "5",
                },
                match_confidence=Decimal("0.72"),
            )
        ],
    )
    prediction = build_blind_prediction(result)
    assert len(prediction["candidates"]) == 3
    candidate = prediction["candidates"][1]
    assert candidate["source_type"] == "web_catalog_hypothesis_unverified"
    assert "未通过 URL" in candidate["conflicts"][0]
    assert prediction["candidates"][2]["source_type"] == "structured_uncertainty"


def test_top3_deduplicates_evidence_wording_but_keeps_identity_conflict() -> None:
    primary = {
        "player_name": "Nolan Traore",
        "season_start": 2025,
        "product_name": "Topps Chrome",
        "card_number": "269",
        "serial_number": 49,
        "serial_total": 50,
        "is_numbered": True,
        "is_rookie": True,
        "rookie_evidence": ["RC"],
    }
    result = CardRecognitionResult(
        **primary,
        confidence=Decimal("0.8"),
        web_candidates=[
            CardWebCandidate(
                title="same card with differently worded RC evidence",
                identity={
                    **primary,
                    "rookie_evidence": ["RC printed on card front"],
                },
                match_confidence=Decimal("0.9"),
            ),
            CardWebCandidate(
                title="unnumbered base refractor alternative",
                identity={
                    "player_name": "Nolan Traore",
                    "season_start": 2025,
                    "product_name": "Topps Chrome",
                    "card_number": "269",
                    "is_rookie": True,
                },
                conflicts=["serial_number", "serial_total", "is_numbered"],
                match_confidence=Decimal("0.7"),
            ),
        ],
    )

    prediction = build_blind_prediction(result)
    assert len(prediction["candidates"]) == 3
    assert prediction["candidates"][1]["title"] == (
        "unnumbered base refractor alternative"
    )
    assert prediction["candidates"][2]["source_type"] == "structured_uncertainty"


def test_exact_identity_confidence_penalizes_missing_anchors() -> None:
    result = CardRecognitionResult(
        player_name="Magic Johnson",
        season_start=2022,
        product_name="Flawless",
        card_number="ALL-MGJ",
        serial_total=25,
        confidence=Decimal("0.95"),
    )
    assert exact_identity_confidence(result) == Decimal("0.88")


def test_high_confidence_catalog_match_fills_primary_identity() -> None:
    result = CardRecognitionResult(
        player_name="Luka Doncic",
        season_start=2023,
        product_name="One and One",
        insert_name="Downtown",
        card_number="25",
        confidence=Decimal("0.9"),
    )
    prediction = build_blind_prediction(
        result,
        [
            {
                "title": "2023-24 One and One Downtown #25 Luka Doncic",
                "identity": {
                    "player_name": "Luka Doncic",
                    "season_start": 2023,
                    "season_end": 2024,
                    "product_name": "One and One",
                    "insert_name": "Downtown",
                    "card_number": "25",
                },
                "matching_fields": [
                    "player_name",
                    "season_start",
                    "product_name",
                    "insert_name",
                    "card_number",
                ],
                "conflicts": [],
                "match_confidence": "0.81",
                "knowledge_entity_id": "card-25",
            }
        ],
    )
    assert prediction["candidates"][0]["identity"]["season_end"] == 2024
    assert prediction["candidates"][0]["knowledge_entity_id"] == "card-25"
    assert len(prediction["candidates"]) == 2
    assert prediction["candidates"][1]["source_type"] == "structured_uncertainty"


def test_high_confidence_web_candidate_only_fills_missing_rich_attributes() -> None:
    result = CardRecognitionResult(
        player_name="Ray Allen",
        season_start=2011,
        product_name="National Treasures",
        card_number="6",
        serial_total=10,
        is_autograph=True,
        autograph_type="on_card",
        confidence=Decimal("0.8"),
        web_candidates=[
            CardWebCandidate(
                title="matching Signature Patches card",
                identity={
                    "player_name": "Ray Allen",
                    "season_start": 2011,
                    "product_name": "National Treasures",
                    "card_number": "6",
                    "serial_total": 10,
                    "is_autograph": False,
                    "is_memorabilia": True,
                    "memorabilia_types": ["patch"],
                    "memorabilia_usage": "player_worn",
                    "is_patch": True,
                },
                match_confidence=Decimal("0.95"),
            )
        ],
    )
    prediction = build_blind_prediction(result)
    identity = prediction["candidates"][0]["identity"]
    assert identity["is_autograph"] is True
    assert identity["is_memorabilia"] is True
    assert identity["memorabilia_types"] == ["patch"]
    assert identity["memorabilia_usage"] == "player_worn"
    assert identity["is_patch"] is True
    assert prediction["candidates"][0]["source_type"] == (
        "visual+web_attribute_enrichment"
    )


def test_web_rich_attribute_overlay_rejects_core_identity_conflict() -> None:
    result = CardRecognitionResult(
        player_name="Ray Allen",
        season_start=2011,
        product_name="National Treasures",
        card_number="6",
        confidence=Decimal("0.8"),
        web_candidates=[
            CardWebCandidate(
                title="different player",
                identity={
                    "player_name": "Kevin Garnett",
                    "season_start": 2011,
                    "product_name": "National Treasures",
                    "card_number": "6",
                    "is_memorabilia": True,
                },
                match_confidence=Decimal("0.99"),
            )
        ],
    )
    prediction = build_blind_prediction(result)
    assert prediction["candidates"][0]["identity"]["is_memorabilia"] is None


def test_parallel_scope_candidates_only_fill_remaining_top3_slots() -> None:
    result = CardRecognitionResult(
        player_name="Nique Clifford",
        season_start=2025,
        product_name="Topps Chrome",
        parallel_name="Chrome",
        serial_total=75,
        confidence=Decimal("0.8"),
        web_candidates=[
            CardWebCandidate(
                title="existing web alternative",
                identity={
                    "player_name": "Nique Clifford",
                    "season_start": 2025,
                    "product_name": "Topps Chrome",
                    "parallel_name": "Blue",
                    "serial_total": 75,
                },
                conflicts=["parallel_name"],
                match_confidence=Decimal("0.8"),
            )
        ],
    )
    scoped = build_parallel_option_candidates(
        result,
        [
            {
                "name": "Purple",
                "canonical_name": "Next Stop Purple /75",
                "matching_fields": ["product_name", "season_start", "serial_total"],
                "conflicts": [],
                "score": "0.75",
                "knowledge_entity_id": "purple",
            },
            {
                "name": "Gold",
                "canonical_name": "Next Stop Gold /75",
                "matching_fields": ["product_name", "season_start", "serial_total"],
                "conflicts": [],
                "score": "0.7",
                "knowledge_entity_id": "gold",
            },
        ],
    )
    prediction = build_blind_prediction(
        result,
        parallel_candidates=scoped,
    )
    assert [item["identity"]["parallel_name"] for item in prediction["candidates"]] == [
        "Chrome",
        "Blue",
        "Purple",
    ]
    assert prediction["candidates"][2]["source_type"] == "knowledge_parallel_scope"


def test_catalog_overlay_does_not_duplicate_parallel_in_insert_name() -> None:
    result = CardRecognitionResult(
        player_name="Stephen Curry",
        product_name="Obsidian",
        insert_name="Supernova-EE",
        parallel_name="Vibrant",
        confidence=Decimal("0.9"),
    )
    prediction = build_blind_prediction(
        result,
        [
            {
                "title": "Supernova-EE Vibrant",
                "identity": {
                    "player_name": "Stephen Curry",
                    "product_name": "Obsidian",
                    "insert_name": "Supernova-EE Vibrant",
                    "parallel_name": "Vibrant",
                },
                "matching_fields": ["player_name", "product_name"],
                "conflicts": [],
                "match_confidence": "0.8",
            }
        ],
    )
    assert prediction["candidates"][0]["identity"]["insert_name"] == "Supernova-EE"


def test_canonicalization_resolves_parallel_alternatives_from_evidence() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            brand_name="Topps",
            product_name="Chrome",
            parallel_name="Gold Refractor / Yellow Refractor",
            confidence=Decimal("0.9"),
            field_evidence={
                "parallel_name": [
                    "Gold Refractor is official; Yellow Refractor is not an official parallel"
                ]
            },
        )
    )
    assert normalized.product_name == "Topps Chrome"
    assert normalized.parallel_name == "Gold"


def test_obsidian_vibrant_one_of_one_is_parallel_not_insert_suffix() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            product_name="Obsidian Basketball",
            insert_name="Supernova-EE Vibrant",
            serial_number=1,
            serial_total=1,
            confidence=Decimal("0.9"),
        )
    )
    assert normalized.insert_name == "Supernova-EE"
    assert normalized.parallel_name == "Electric Etch Vibrant Mojo Flood"


def test_nolan_traore_card_is_normalized_from_official_checklist() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Nolan Traore",
            season_start=2024,
            season_end=2025,
            product_name="Topps Chrome",
            card_number="269",
            serial_number=49,
            serial_total=50,
            parallel_name="Gold",
            is_rookie=True,
            rookie_evidence=["RC", "2023"],
            confidence=Decimal("0.9"),
        )
    )
    assert normalized.season_start == 2025
    assert normalized.season_end == 2026
    assert normalized.parallel_name == "Gold"
    assert normalized.rookie_evidence == ["RC"]


def test_grade_is_preserved_and_company_is_normalized() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Stephen Curry",
            grade_company="psa grading",
            grade_value=Decimal("10"),
            confidence=Decimal("0.9"),
        )
    )
    prediction = build_blind_prediction(normalized)
    assert normalized.grade_company == "PSA"
    assert prediction["candidates"][0]["identity"]["grade_value"] == "10"


def test_identity_canonicalization_moves_rc_and_fractional_number() -> None:
    result = CardRecognitionResult(
        player_name="Victor Wembanyama",
        card_number="1/1",
        parallel_name="RC",
        confidence=Decimal("0.9"),
    )
    normalized = canonicalize_identity(result)
    assert normalized.card_number is None
    assert normalized.serial_number == 1
    assert normalized.serial_total == 1
    assert normalized.parallel_name is None
    assert normalized.is_rookie is True
    assert normalized.confidence == Decimal("0.78")


def test_fractional_card_number_is_cleared_when_serial_is_already_present() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Jerry West",
            card_number="07/10",
            serial_number=7,
            serial_total=10,
            confidence=Decimal("0.9"),
        )
    )
    assert normalized.card_number is None
    assert normalized.serial_number == 7
    assert normalized.serial_total == 10


def test_topps_manufacturing_code_is_replaced_by_subset_checklist_number() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Paolo Banchero",
            brand_name="Topps",
            product_name="NBA Hoops",
            card_number="CMP132931",
            confidence=Decimal("0.96"),
            field_evidence={
                "card_number": [
                    "背面左上角 O-17 为 Oasis 系列清单编号；底部 CODE#CMP132931 为厂内编码"
                ]
            },
        )
    )

    assert normalized.card_number == "O-17"
    assert any("厂内 CODE/CMP" in caveat for caveat in normalized.caveats)


def test_letter_suffix_topps_autograph_code_replaces_jersey_number() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Alex Sarr",
            brand_name="Topps",
            product_name="Topps Chrome",
            card_number="20",
            insert_name="Autograph Card",
            is_autograph=True,
            confidence=Decimal("0.96"),
            field_evidence={
                "card_number": ["背面右上黑框真实卡号 TCA-AS，20 是球衣号码"],
                "is_autograph": [
                    "TOPPS CERTIFIED AUTOGRAPH ISSUE; guarantees authenticity"
                ],
            },
        )
    )

    assert normalized.card_number == "TCA-AS"
    assert normalized.is_autograph is True


def test_prefixed_code_in_insert_field_moves_to_card_number() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Joel Embiid",
            brand_name="Topps",
            product_name="Topps Chrome",
            card_number="CMP144337",
            insert_name="SE-8",
            confidence=Decimal("0.96"),
        )
    )

    assert normalized.card_number == "SE-8"
    assert normalized.insert_name is None


def test_non_player_signature_is_not_an_autograph_card() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Jason Terry",
            product_name="Hardcourt",
            insert_name="UD Game Floor",
            is_autograph=True,
            autograph_type="on_card",
            confidence=Decimal("0.9"),
            field_evidence={
                "is_autograph": [
                    "signature is the company president, not a player autograph"
                ]
            },
        )
    )

    assert normalized.is_autograph is False
    assert normalized.autograph_type == "none"


def test_long_numeric_topps_code_is_replaced_by_visible_prefixed_number() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Jayson Tatum",
            brand_name="Topps",
            product_name="NBA Hoops",
            card_number="132874",
            confidence=Decimal("0.96"),
            field_evidence={
                "card_number": ["背面顶部编号 BH-10；底部 CODE#132874 不是清单卡号"]
            },
        )
    )

    assert normalized.card_number == "BH-10"


def test_topps_nba_product_season_uses_copyright_end_year() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Trae Young",
            brand_name="Topps",
            product_name="NBA Hoops",
            season_start=2024,
            season_end=2025,
            copyright_year=2026,
            card_number="BH-8",
            confidence=Decimal("0.96"),
        )
    )

    assert (normalized.season_start, normalized.season_end) == (2025, 2026)
    assert any("版权结束年" in caveat for caveat in normalized.caveats)


def test_prepare_vision_image_bounds_large_payload() -> None:
    image = np.full((2200, 3200, 3), 127, dtype=np.uint8)
    success, encoded = cv2.imencode(".jpg", image)
    assert success
    content, content_type = prepare_vision_image(encoded.tobytes(), "image/jpeg")
    decoded = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert content_type == "image/jpeg"
    assert max(decoded.shape[:2]) <= 1800


def test_benchmark_remote_limit_accepts_normal_twelve_megapixel_jpeg() -> None:
    assert BENCHMARK_REMOTE_MAX_BYTES > 12_873_056


@pytest.mark.parametrize("product", ["Contenders Optic", "Mosaic Basketball"])
def test_bare_prizm_logo_on_unnumbered_panini_insert_is_not_a_parallel(
    product: str,
) -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Jayson Tatum",
            season_start=2019,
            season_end=2020,
            brand_name="Panini",
            product_name=product,
            insert_name="Superstar",
            card_number="8",
            parallel_name="Prizm",
            is_numbered=False,
            confidence=Decimal("0.98"),
            field_evidence={"parallel_name": ["背面右下角印有 PRIZM logo"]},
        )
    )

    assert normalized.parallel_name is None
    assert any(
        "generic_panini_prizm_logo_removed" in caveat
        for caveat in normalized.caveats
    )


def test_specific_numbered_optic_parallel_is_preserved() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            product_name="Contenders Optic",
            insert_name="Superstar",
            parallel_name="Gold",
            serial_number=3,
            serial_total=10,
            confidence=Decimal("0.95"),
        )
    )

    assert normalized.parallel_name == "Gold"


def test_canonicalize_obscured_insert_transcription() -> None:
    normalized = canonicalize_identity(
        CardRecognitionResult(
            player_name="Luka Dončić",
            product_name="NBA Hoops",
            insert_name="Hoop-Itic",
            card_number="HN-10",
            confidence="0.83",
        )
    )

    assert normalized.insert_name == "Hoopnotic"
    assert any("遮挡转写别名" in caveat for caveat in normalized.caveats)


def test_team_golden_text_is_not_a_parallel_or_numbering_evidence():
    # Real failure: GOLDEN from GOLDEN STATE WARRIORS was promoted into a
    # parallel; unsourced catalog prose then repeated the same OCR error.
    result = canonicalize_identity(CardRecognitionResult(
        player_name='Stephen Curry', product_name='STATUS BASKETBALL',
        parallel_name='GOLDEN', is_numbered=True, confidence=Decimal('.95'),
        field_evidence={'parallel_name': [
            "正面：GOLDEN（球衣/边框配色暗示金色平行，正面可见 GOLDEN 字样）",
            "联网候选：官方 checklist 中该卡明确列为 GOLDEN parallel",
        ]},
    ))
    assert result.parallel_name is None
    assert result.is_numbered is None
    assert any('team_text_not_parallel' in c for c in result.caveats)


@pytest.mark.parametrize('parallel,evidence', [
    ('Gold', '正面金色折射，独立限编 5/10'),
    ('Golden Treasures', '背面独立版本标记 Golden Treasures 1/1'),
    ('Blue', '正面蓝色边框；球队 GOLDEN STATE WARRIORS'),
])
def test_team_text_guard_preserves_specific_independent_parallel_evidence(parallel,evidence):
    result = canonicalize_identity(CardRecognitionResult(
        player_name='Stephen Curry', product_name='Status', parallel_name=parallel,
        confidence=Decimal('.9'), field_evidence={'parallel_name':[evidence]},
    ))
    assert result.parallel_name == parallel
