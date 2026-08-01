from datetime import datetime, timezone
from decimal import Decimal

from card_platform.market_data.models import EvidenceRecord, MarketIdentity
from card_platform.market_data.protection import retry_after_seconds
from card_platform.market_data.service import MarketDataService


def _identity(**updates) -> MarketIdentity:
    values = {
        "player_name": "Tim Duncan",
        "season_start": 2021,
        "season_end": 2022,
        "brand_name": "Panini",
        "product_name": "Immaculate",
        "card_number": "TD-21",
        "parallel_name": "Gold",
        "serial_total": 10,
        "is_autograph": True,
        "is_patch": True,
        "grade_company": None,
        "grade_value": None,
    }
    values.update(updates)
    return MarketIdentity(**values)


def test_identity_fingerprint_is_normalized_and_isolated() -> None:
    first = _identity(player_name="  TIM   DUNCAN ", parallel_name="GOLD")
    same = _identity(player_name="tim duncan", parallel_name="gold")
    different = _identity(card_number="TD-22")
    assert first.fingerprint == same.fingerprint
    assert first.fingerprint != different.fingerprint


def test_identity_fingerprint_keeps_grade_and_serial_boundaries() -> None:
    raw = _identity()
    graded = _identity(grade_company="PSA", grade_value=Decimal("10"))
    other_print_run = _identity(serial_total=25)
    assert len({raw.fingerprint, graded.fingerprint, other_print_run.fingerprint}) == 3


def test_popularity_ttl_is_shorter_for_hot_cards() -> None:
    assert MarketDataService.ttl_seconds(100) < MarketDataService.ttl_seconds(20)
    assert MarketDataService.ttl_seconds(20) < MarketDataService.ttl_seconds(0)


def test_retry_after_supports_seconds_and_invalid_values() -> None:
    assert retry_after_seconds("120") == 120
    assert retry_after_seconds("invalid") is None
    assert retry_after_seconds(None) is None


def test_evidence_model_preserves_shipping_and_raw_identity_result() -> None:
    evidence = EvidenceRecord(
        provider_code="authorized_test",
        external_sale_id="sale-1",
        raw_title="Original listing title",
        sale_price=Decimal("100"),
        shipping_price=Decimal("12"),
        currency="USD",
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_url="https://example.com/sale-1",
        evidence_level="confirmed_transaction",
        match_confidence=Decimal("0.9"),
        identity_classification={"tier": "exact", "version": 1},
        raw_payload={"provider_field": "preserved"},
        fetched_at=datetime.now(timezone.utc),
    )
    assert evidence.shipping_price == Decimal("12")
    assert evidence.identity_classification["tier"] == "exact"
    assert evidence.raw_payload["provider_field"] == "preserved"


def test_migration_declares_database_refresh_coalescing_constraint() -> None:
    migration = (
        __import__("pathlib")
        .Path(__file__)
        .parents[3]
        .joinpath("db/migrations/0011_market_data_platform.sql")
        .read_text()
    )
    assert "market_refresh_job_active_identity_uidx" in migration
    assert "WHERE status IN ('queued', 'running')" in migration
    assert "FOR UPDATE OF j SKIP LOCKED" not in migration
