import pytest

from card_platform.flatbed_matching import CardRegion, propose_pairs


def region(identifier: str, x: float, y: float, **signals) -> CardRegion:
    return CardRegion(identifier, x, y, 0.18, 0.28, signals=signals)


def test_global_matching_handles_horizontal_flip_and_shuffled_order() -> None:
    fronts = [region("f1", 0.1, 0.1), region("f2", 0.7, 0.1), region("f3", 0.1, 0.6)]
    backs = [region("b3", 0.7, 0.6), region("b1", 0.7, 0.1), region("b2", 0.1, 0.1)]
    result = propose_pairs(fronts, backs)
    assert result["transform"] == "mirror_x"
    assert {(pair["front_id"], pair["back_id"]) for pair in result["pairs"]} == {
        ("f1", "b1"),
        ("f2", "b2"),
        ("f3", "b3"),
    }


@pytest.mark.parametrize(
    ("transform", "backs"),
    [
        ("mirror_y", [(0.1, 0.62), (0.68, 0.62), (0.1, 0.12)]),
        ("rotate_180", [(0.72, 0.62), (0.14, 0.62), (0.72, 0.12)]),
    ],
)
def test_global_matching_handles_vertical_and_180_degree_layouts(
    transform: str, backs: list[tuple[float, float]]
) -> None:
    fronts = [region("f1", 0.1, 0.1), region("f2", 0.68, 0.1), region("f3", 0.1, 0.6)]
    back_regions = [region(f"b{index}", x, y) for index, (x, y) in enumerate(backs, 1)]
    result = propose_pairs(fronts, back_regions)
    assert result["transform"] == transform


def test_ocr_disambiguates_duplicate_layout_candidates_globally() -> None:
    fronts = [
        region("f1", 0.2, 0.2, card_number="12"),
        region("f2", 0.22, 0.2, card_number="77"),
    ]
    backs = [
        region("b2", 0.2, 0.2, card_number="77"),
        region("b1", 0.22, 0.2, card_number="12"),
    ]
    result = propose_pairs(fronts, backs)
    assert [(pair["front_id"], pair["back_id"]) for pair in result["pairs"]] == [
        ("f1", "b1"),
        ("f2", "b2"),
    ]


def test_quantity_mismatch_is_explicit_and_requires_review() -> None:
    result = propose_pairs(
        [region("f1", 0.1, 0.1), region("f2", 0.6, 0.1)],
        [region("b1", 0.1, 0.1)],
    )
    assert result["missing_back"] == ["f2"]
    assert result["missing_front"] == []
    assert result["requires_review"] is True


def test_low_confidence_conflict_never_silently_auto_pairs() -> None:
    result = propose_pairs(
        [region("f1", 0.05, 0.05, card_number="1")],
        [region("b1", 0.45, 0.45, card_number="2")],
    )
    assert result["pairs"][0]["requires_review"] is True
    assert result["pairs"][0]["conflicts"]


def test_unresolved_crop_never_silently_auto_pairs() -> None:
    front = CardRegion(
        "f1",
        0.1,
        0.1,
        0.18,
        0.28,
        failure_reason="card_outline_unresolved",
    )
    back = CardRegion("b1", 0.1, 0.1, 0.18, 0.28)

    pair = propose_pairs([front], [back])["pairs"][0]

    assert pair["requires_review"] is True
    assert "front_crop_card_outline_unresolved" in pair["conflicts"]


def test_independent_crop_quality_error_never_silently_auto_pairs() -> None:
    front = region("f1", 0.1, 0.1)
    back = region(
        "b1",
        0.1,
        0.1,
        crop_quality={
            "usability_score": 0.18,
            "usable": False,
            "violations": [
                {"code": "blank_or_fragment", "severity": "error"}
            ],
        },
    )

    pair = propose_pairs([front], [back])["pairs"][0]

    assert pair["signals"]["crop_quality"] == 0.18
    assert pair["requires_review"] is True
    assert "back_crop_quality_blank_or_fragment" in pair["conflicts"]


def test_ocr_name_containment_ignores_back_stats_and_tolerates_ocr_noise() -> None:
    result = propose_pairs(
        [region("f1", 0.1, 0.1, ocr_tokens=["shareet", "abdur", "pahim", "grizzlies"])],
        [
            region(
                "b1",
                0.1,
                0.1,
                ocr_tokens=[
                    "shareef", "abdur", "rahim", "grizzlies", "year", "totals",
                    "1996", "2001", "drafted", "college",
                ],
            )
        ],
    )
    pair = result["pairs"][0]
    assert pair["signals"]["ocr"] >= 0.75
    assert "ocr_identity_conflict" not in pair["conflicts"]
