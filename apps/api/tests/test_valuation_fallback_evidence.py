from datetime import datetime, timezone
from decimal import Decimal

from card_platform.valuation_worker import (
    ValuationReference,
    is_formal_fallback_reference,
)


def reference(*, evidence_type: str, occurred_at: datetime | None) -> ValuationReference:
    return ValuationReference(
        title="Public evidence",
        source_url="https://example.com/evidence",
        evidence_type=evidence_type,
        occurred_at=occurred_at,
        observed_price_base=Decimal("10"),
        match_confidence=Decimal("0.8"),
        adjustment="none",
        condition_type="raw",
        player_name="Example Player",
    )


def test_price_guide_cannot_become_formal_fallback_valuation() -> None:
    assert not is_formal_fallback_reference(
        reference(evidence_type="price_guide", occurred_at=datetime.now(timezone.utc))
    )


def test_transaction_label_without_sale_time_cannot_become_formal_fallback() -> None:
    assert not is_formal_fallback_reference(
        reference(evidence_type="aggregated_sale", occurred_at=None)
    )


def test_dated_completed_sale_can_become_formal_fallback_reference() -> None:
    assert is_formal_fallback_reference(
        reference(
            evidence_type="public_completed_sale",
            occurred_at=datetime.now(timezone.utc),
        )
    )
