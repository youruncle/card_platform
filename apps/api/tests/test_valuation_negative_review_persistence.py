import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from card_platform.comparable_image_verifier import (
    VisualCandidate,
    current_persisted_visual,
    visual_asset_binding,
)
from card_platform.valuation_trace import content_hash
from card_platform.valuation_worker import (
    AutomatedValuationWorker,
    ComparableSearchResult,
    DiscoveredComparable,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous_verdict,previous_confidence,requires_review",
    [
        ("different", ".95", True),
        ("same", ".95", True),
        ("compatible", ".95", True),
        ("uncertain", ".95", True),
        ("uncertain", "0", False),
    ],
)
async def test_store_failed_reverification_preserves_review_quarantine_until_bound_same(
    previous_verdict, previous_confidence, requires_review
):
    if "TEST_DATABASE_URL" not in os.environ:
        pytest.skip("isolated PostgreSQL required")
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    async with await AsyncConnection.connect(
        os.environ["TEST_DATABASE_URL"], row_factory=dict_row
    ) as conn:
        await conn.execute(
            "CREATE TEMP TABLE market_comp (LIKE public.market_comp INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES)"
        )
        await conn.commit()

        class Database:
            @asynccontextmanager
            async def connection(self):
                yield conn

        worker = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
        worker.database = Database()
        identity = {
            "card_definition_id": uuid4(),
            "image_front_url": "/uploads/front.jpg",
            "image_content_hashes": {"front": content_hash(b"front"), "back": None},
        }
        candidate = VisualCandidate(
            "https://cardhobby.com.cn/Market/Item/123456789",
            "Example card",
            "https://images.cardhobby.com/example.jpg",
        )
        base = dict(
            source_code="cardhobby",
            title=candidate.title,
            source_url=candidate.id,
            image_url=candidate.image_url,
            occurred_at="2026-09-01T00:00:00+08:00",
            sale_price="10",
            currency="CNY",
            fx_rate_to_base="1",
            match_confidence=".95",
        )

        async def store(verdict, binding=None, confidence=".95"):
            await worker._store_comparables(
                identity,
                ComparableSearchResult(
                    comparables=[
                        DiscoveredComparable(
                            **base,
                            visual_verdict=verdict,
                            visual_confidence=confidence,
                            visual_evidence_binding=binding,
                        )
                    ]
                ),
            )
            cursor = await conn.execute("SELECT raw_payload FROM market_comp")
            return (await cursor.fetchone())["raw_payload"]

        await store(previous_verdict, confidence=previous_confidence)
        for verdict in [None, "uncertain"]:
            raw = await store(verdict, confidence="0")
            assert (raw.get("visual_review_required") is True) == requires_review
            assert (
                current_persisted_visual(identity, candidate, raw)["visual_review_required"]
                == requires_review
            )
        raw = await store(
            "same", visual_asset_binding(identity, candidate, b"front", None, b"candidate")
        )
        current = current_persisted_visual(identity, candidate, raw)
        assert current["visual_verdict"] == "same"
        assert current.get("visual_review_required") is not True
        # Approval belongs to this captured review, not to all future observations.
        # A later failed verification must not inherit a permanent "same" approval.
        raw = await store(None)
        assert current_persisted_visual(identity, candidate, raw)["visual_review_required"] is True


def test_current_raw_negative_cannot_be_hidden_by_old_snapshot_positive():
    from scripts.audit_cardhobby_valuation import select_frozen_visual

    result = select_frozen_visual({"visual_verdict": "different"}, {"visual_verdict": "same"})
    assert result["visual_verdict"] == "different"
    assert select_frozen_visual({}, {"visual_verdict": "same"})["visual_verdict"] == "same"


@pytest.mark.parametrize("previous_verdict", ["same", "compatible", "uncertain"])
def test_failed_current_review_does_not_erase_previous_review_requirement(previous_verdict):
    from scripts.audit_cardhobby_valuation import select_frozen_visual

    assert (
        select_frozen_visual(
            {"visual_verdict": None},
            {"visual_verdict": previous_verdict, "visual_confidence": ".95"},
        )["visual_review_required"]
        is True
    )
