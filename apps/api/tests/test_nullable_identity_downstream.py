"""Incomplete private intake must not become a public market identity or price."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from card_platform.player_prices.subjects import SubjectIndex
from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.valuation_worker import AutomatedValuationWorker


def test_anonymous_private_players_are_not_public_market_subjects():
    players = [
        {"id": uuid4(), "full_name": name, "birth_date": None,
         "nba_person_id": None, "created_at": datetime.now(timezone.utc)}
        for name in (None, "", "   ", "Stephen Curry")
    ]
    index = SubjectIndex(players)
    assert [subject["full_name"] for subject in index.for_ids([p["id"] for p in players])] == ["Stephen Curry"]
    assert all(index.get(player["id"]) is None for player in players[:3])


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["player_name", "brand_name", "product_name", "season_start"])
async def test_manual_valuation_enqueue_with_unknown_core_finishes_pending_without_research_or_prices(missing):
    identity = {"player_name": "Stephen Curry", "brand_name": "Topps",
                "product_name": "Topps Basketball", "season_start": 2025,
                "card_number": "SA-5", "collection_item_id": uuid4()}
    identity[missing] = None
    writes = []

    class Database:
        @asynccontextmanager
        async def connection(self):
            yield self

        async def execute(self, sql, params):
            writes.append((sql, params))

    worker = object.__new__(AutomatedValuationWorker)
    worker.database = Database()
    worker.knowledge_runtime = None

    async def load(_job_id):
        return identity

    async def external(*_args, **_kwargs):
        raise AssertionError("unknown core must not reach external identity research or market search")

    worker._load_identity = load
    worker._start_identity_research = external
    worker._search = external
    await worker._process(uuid4())
    assert len(writes) == 1
    sql, params = writes[0]
    assert "UPDATE valuation_job" in sql
    assert params[0] == "insufficient_evidence"
    assert params[1].obj["missing_identity_fields"] == [missing]
    assert params[1].obj["identity_resolution_status"] == "pending"
    assert params[2] is None


@pytest.mark.parametrize("flag", ["is_rookie", "is_autograph", "is_memorabilia", "is_patch", "is_numbered"])
@pytest.mark.parametrize("value", [None, False, True])
def test_identity_resolver_preserves_nullable_attribute_values_without_defaulting_unknown_false(flag, value):
    result = CardIdentityResolver().resolve({"player_name": "Stephen Curry", flag: value})
    assert result.as_dict()[flag] is value


@pytest.mark.parametrize("evidence,flag", [
    ({"parallel_name": "RC"}, "is_rookie"),
    ({"insert_name": "Rookie Patch Autographs"}, "is_autograph"),
    ({"insert_name": "Prime Relic Autographs"}, "is_memorabilia"),
    ({"is_patch": True}, "is_memorabilia"),
])
def test_identity_resolver_known_attribute_evidence_still_establishes_true(evidence, flag):
    result = CardIdentityResolver().resolve({"player_name": "Stephen Curry", flag: None, **evidence})
    assert result.as_dict()[flag] is True
