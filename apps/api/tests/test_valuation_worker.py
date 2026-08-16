from decimal import Decimal
import asyncio
from uuid import uuid4

from card_platform.valuation_worker import (
    AutomatedValuationWorker,
    DiscoveredComparable,
    FallbackValuation,
    PlayerAdjustment,
    PlayerPricePair,
    ValuationReference,
)


def _fallback(reference_count: int, confidence: str = "0.60") -> FallbackValuation:
    references = [
        ValuationReference(
            title=f"Reference {index}",
            source_url=f"https://example.com/{index}",
            evidence_type="analog_sale",
            observed_price_base=Decimal("100"),
            match_confidence=Decimal("0.7"),
            adjustment="model analogy",
            condition_type="raw",
            player_name="Example Player",
        )
        for index in range(reference_count)
    ]
    return FallbackValuation(
        low_value=Decimal("70"),
        fair_value=Decimal("100"),
        high_value=Decimal("140"),
        confidence=Decimal(confidence),
        methodology="model prior",
        references=references,
    )


def test_model_fallback_confidence_is_capped_by_verifiable_references() -> None:
    confidence = AutomatedValuationWorker._model_fallback_confidence
    assert confidence(_fallback(0)) == Decimal("0.15")
    assert confidence(_fallback(1)) == Decimal("0.25")
    assert confidence(_fallback(2)) == Decimal("0.35")
    assert confidence(_fallback(2, "0.20")) == Decimal("0.20")


def test_raw_card_rejects_all_graded_references() -> None:
    identity = {"grade_company": None, "grade_value": None}
    raw = ValuationReference(
        title="Raw sale",
        source_url="https://example.com/raw",
        evidence_type="analog_sale",
        observed_price_base=Decimal("100"),
        match_confidence=Decimal("0.8"),
        adjustment="same raw condition",
        condition_type="raw",
        player_name="Tim Duncan",
    )
    graded = raw.model_copy(
        update={
            "title": "PSA 10 sale",
            "condition_type": "graded",
            "grade_company": "PSA",
            "grade_value": Decimal("10"),
        }
    )
    assert AutomatedValuationWorker._grade_matches(identity, raw)
    assert not AutomatedValuationWorker._grade_matches(identity, graded)


def test_graded_card_requires_exact_company_and_grade() -> None:
    identity = {"grade_company": "PSA", "grade_value": Decimal("10")}
    exact = DiscoveredComparable(
        title="PSA 10 sale",
        source_url="https://example.com/psa10",
        occurred_at="2026-01-01T00:00:00Z",
        sale_price=Decimal("500"),
        currency="USD",
        fx_rate_to_base=Decimal("7"),
        grade_company="PSA",
        grade_value=Decimal("10"),
        match_confidence=Decimal("0.9"),
    )
    assert AutomatedValuationWorker._comparable_grade_matches(identity, exact)
    assert not AutomatedValuationWorker._comparable_grade_matches(
        identity, exact.model_copy(update={"grade_value": Decimal("9")})
    )
    assert not AutomatedValuationWorker._comparable_grade_matches(
        identity, exact.model_copy(update={"grade_company": "BGS"})
    )


def test_cross_player_coefficient_uses_median_of_at_least_three_pairs() -> None:
    identity = {
        "player_name": "Tim Duncan",
        "grade_company": None,
        "grade_value": None,
    }
    ratios = [(84, 100), (170, 200), (82, 100)]
    pairs = [
        PlayerPricePair(
            target_title=f"Duncan comp {index}",
            target_url=f"https://example.com/duncan-{index}",
            target_price_base=Decimal(target),
            reference_title=f"Giannis comp {index}",
            reference_url=f"https://example.com/giannis-{index}",
            reference_price_base=Decimal(reference),
            condition_type="raw",
        )
        for index, (target, reference) in enumerate(ratios)
    ]
    coefficients = AutomatedValuationWorker.build_player_coefficients(
        identity,
        [
            PlayerAdjustment(
                target_player="Tim Duncan",
                reference_player="Giannis Antetokounmpo",
                segment="premium patch /26-/75 raw",
                pairs=pairs,
            )
        ],
    )
    result = coefficients["giannis antetokounmpo"]
    assert result["coefficient"] == Decimal("0.84")
    assert result["sample_count"] == 3


def test_valuation_process_requires_confirmed_identity_even_when_fields_are_specific() -> None:
    worker = object.__new__(AutomatedValuationWorker)
    calls = []

    async def load_identity(_job_id):
        return {
            "collection_item_id": uuid4(), "player_name": "Stephen Curry",
            "season_start": 2025, "season_end": 2026,
            "product_name": "Topps Basketball", "card_number": "SA-5",
            "insert_name": "Stars of the NBA", "parallel_name": None,
            "serial_number": None, "serial_total": None,
            "latest_identity_review_status": None,
            "confirmed_identity_version_id": None,
        }

    async def start_research(_identity, blockers):
        calls.append(("research", blockers))
        return {"status": "awaiting_confirmation", "case_id": "case", "candidate_count": 1}

    async def finish(_job_id, status, summary, *_args):
        calls.append(("finish", status, summary))

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("unconfirmed identity must not reach market search or pricing")

    worker._load_identity = load_identity
    worker._start_identity_research = start_research
    worker._finish = finish
    worker._search = forbidden
    worker._calculate = forbidden
    asyncio.run(worker._process(uuid4()))

    assert calls[0] == ("research", ["identity_confirmation_required"])
    assert calls[1][1] == "insufficient_evidence"
    assert calls[1][2]["identity_review_status"] == "not_created"
