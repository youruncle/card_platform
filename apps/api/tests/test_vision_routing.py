import asyncio
from decimal import Decimal

from card_platform.schemas import CardRecognitionResult
from card_platform.vision_routing import (
    TieredVisionRouter,
    finalize_recognition_status,
    recognition_quality_issues,
    sanitize_birthdate_serial_false_positive,
)


def result(**updates) -> CardRecognitionResult:
    payload = {
        "player_name": "Stephen Curry",
        "season_start": 2025,
        "season_end": 2026,
        "brand_name": "Topps",
        "product_name": "Topps Basketball",
        "card_number": "SA-5",
        "insert_name": "Stars of the NBA",
        "parallel_name": None,
        "confidence": "0.95",
    }
    payload.update(updates)
    return CardRecognitionResult.model_validate(payload)


def provider(value: CardRecognitionResult, usage=(10_000, 1_000), calls=None):
    async def call(prior):
        if calls is not None:
            calls.append(prior)
        return value, {"prompt_tokens": usage[0], "completion_tokens": usage[1]}
    return call


def test_high_quality_flash_does_not_escalate() -> None:
    calls = []
    router = TieredVisionRouter(
        provider(result(), calls=calls),
        provider(result(confidence="0.99"), calls=calls),
        provider(result(confidence="0.99"), calls=calls),
    )
    resolved = asyncio.run(router.recognize())
    assert resolved.recognition_model == "qwen3-vl-flash"
    assert len(resolved.recognition_route) == 1
    assert resolved.recognition_estimated_cost_cny == Decimal("0.003000")
    assert resolved.recognition_status == "resolved"


def test_missing_identity_escalates_to_plus_and_passes_prior_result() -> None:
    plus_calls = []
    router = TieredVisionRouter(
        provider(result(card_number=None, confidence="0.82")),
        provider(result(confidence="0.96"), calls=plus_calls),
        provider(result(confidence="0.99")),
    )
    resolved = asyncio.run(router.recognize())
    assert resolved.recognition_model == "qwen3-vl-plus"
    assert len(resolved.recognition_route) == 2
    assert plus_calls[0] is not None
    assert resolved.recognition_estimated_cost_cny == Decimal("0.023000")


def test_generic_parallel_stops_at_plus_for_checklist_research() -> None:
    router = TieredVisionRouter(
        provider(result(parallel_name="Prizm", confidence="0.90")),
        provider(result(parallel_name="Refractor", confidence="0.91")),
        provider(result(parallel_name="Gold Diamante", confidence="0.97")),
    )
    resolved = asyncio.run(router.recognize())
    assert resolved.recognition_model == "qwen3-vl-plus"
    assert len(resolved.recognition_route) == 2
    assert resolved.recognition_status == "research_required"


def test_plus_missing_card_number_escalates_to_max() -> None:
    router = TieredVisionRouter(
        provider(result(card_number=None, confidence="0.82")),
        provider(result(card_number=None, confidence="0.90")),
        provider(result(confidence="0.97")),
    )
    resolved = asyncio.run(router.recognize())
    assert resolved.recognition_model == "qwen3.8-max"
    assert len(resolved.recognition_route) == 3
    assert resolved.recognition_status == "resolved"


def test_partial_serial_fraction_is_a_hard_quality_issue() -> None:
    assert "partial_serial_fraction" in recognition_quality_issues(
        result(serial_number=1, serial_total=None)
    )


def test_missing_brand_or_season_end_cannot_auto_resolve() -> None:
    assert "missing_brand_name" in recognition_quality_issues(result(brand_name=None))
    assert "missing_season_end" in recognition_quality_issues(result(season_end=None))


def test_cross_product_prizm_name_is_a_hard_quality_issue() -> None:
    assert "parallel_product_technology_conflict" in recognition_quality_issues(
        result(product_name="Flagship", parallel_name="Gold Prizm")
    )


def test_color_or_serial_only_parallel_is_underspecified() -> None:
    assert "parallel_contains_serial_fraction" in recognition_quality_issues(
        result(parallel_name="Gold /50", serial_number=27, serial_total=50)
    )


def test_birthdate_fraction_is_not_accepted_as_serial_number() -> None:
    cleaned = sanitize_birthdate_serial_false_positive(result(
        serial_number=3,
        serial_total=6,
        is_numbered=True,
        field_evidence={"serial_number": ["Back text: BORN: 3/6/73, MELROSE PARK"]},
    ))
    assert cleaned.serial_number is None
    assert cleaned.serial_total is None
    assert cleaned.is_numbered is None
    assert "birthdate_serial_false_positive" in recognition_quality_issues(cleaned)
    assert finalize_recognition_status(cleaned).recognition_status == "research_required"


def test_independently_evidenced_serial_is_preserved() -> None:
    cleaned = sanitize_birthdate_serial_false_positive(result(
        serial_number=24,
        serial_total=199,
        is_numbered=True,
        field_evidence={"serial_number": ["Front foil stamp: 024/199"]},
    ))
    assert (cleaned.serial_number, cleaned.serial_total, cleaned.is_numbered) == (24, 199, True)


def test_independent_visible_stamp_survives_same_card_birthdate_text() -> None:
    cleaned = sanitize_birthdate_serial_false_positive(result(
        serial_number=3,
        serial_total=6,
        is_numbered=True,
        field_evidence={
            "serial_number": [
                "Back biography: BORN: 3/6/73, MELROSE PARK",
                "Front foil serial stamp: 03/06",
            ]
        },
    ))

    assert (cleaned.serial_number, cleaned.serial_total, cleaned.is_numbered) == (3, 6, True)


def test_twenty_of_twenty_cannot_be_removed_as_birthday_prefix() -> None:
    cleaned = sanitize_birthdate_serial_false_positive(result(
        serial_number=20,
        serial_total=20,
        is_numbered=True,
        caveats=["OCR ambiguity: BORN: 20/20/80"],
    ))

    assert (cleaned.serial_number, cleaned.serial_total, cleaned.is_numbered) == (20, 20, True)


def test_flash_provider_failure_falls_forward_to_plus() -> None:
    async def failed(_prior):
        raise TimeoutError("flash timeout")

    router = TieredVisionRouter(failed, provider(result()), None)
    resolved = asyncio.run(router.recognize())
    assert resolved.recognition_model == "qwen3-vl-plus"
    assert resolved.recognition_route[0]["status"] == "failed"
    assert resolved.recognition_route[1]["status"] == "succeeded"
    assert "numbered_parallel_name_underspecified" in recognition_quality_issues(
        result(parallel_name="Gold", serial_number=27, serial_total=50)
    )
