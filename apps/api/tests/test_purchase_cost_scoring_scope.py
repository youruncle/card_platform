"""Repeated matching reuses pure scores only inside one current operation."""

import asyncio
from copy import deepcopy

import pytest

from card_platform.knowledge import KnowledgeResolver
from card_platform.purchase_cost import score_match
from card_platform.purchase_cost_catalog import score_from_product
from card_platform.purchase_cost_knowledge import purchase_cost_knowledge_scope
from test_purchase_cost_archive import line


CARD = dict(
    player_name="Example Player",
    brand_name="Topps",
    product_name="Chrome",
    season_start=2025,
    card_number="7",
    serial_number=3,
    serial_total=99,
    image_content_hashes={"front": "a" * 64},
)
LINE = {**line(), "title": "2025 Topps Chrome Example Player #7 03/99"}


@pytest.mark.asyncio
async def test_repeated_pure_score_is_reused_without_returning_mutable_cached_lists():
    knowledge = KnowledgeResolver()
    calls = []

    def scorer(*args, **kwargs):
        calls.append(1)
        return score_match(*args, **kwargs)

    with purchase_cost_knowledge_scope():
        first = score_from_product(CARD, LINE, knowledge, scorer=scorer)
        first["reasons"].append("caller mutation")
        second = score_from_product(CARD, LINE, knowledge, scorer=scorer)
        assert "caller mutation" not in second["reasons"]
        assert not second["conflicts"]
        assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["serial", "title", "image_hash", "visual"])
async def test_current_identity_source_and_visual_changes_invalidate_pure_score(changed):
    knowledge = KnowledgeResolver()
    calls = []
    card = deepcopy(CARD)
    source = deepcopy(LINE)

    def scorer(*args, **kwargs):
        calls.append(1)
        return score_match(*args, **kwargs)

    with purchase_cost_knowledge_scope():
        score_from_product(card, source, knowledge, scorer=scorer)
        visual = None
        if changed == "serial":
            card["serial_number"] = 4
        elif changed == "title":
            source["title"] = source["title"].replace("03/99", "04/99")
        elif changed == "image_hash":
            card["image_content_hashes"]["front"] = "b" * 64
        else:
            visual = {"verdict": "different"}
        result = score_from_product(card, source, knowledge, scorer=scorer, visual_evidence=visual)
        assert len(calls) == 2
        if changed in {"serial", "title"}:
            assert "限编分子冲突" in result["conflicts"]
        if changed == "visual":
            assert "逐图复核确认非同款" in result["conflicts"]


@pytest.mark.asyncio
async def test_scores_do_not_cross_child_tasks_operations_or_resolver_versions():
    knowledge = KnowledgeResolver({"version": 1})
    calls = []

    def scorer(*args, **kwargs):
        calls.append(1)
        return score_match(*args, **kwargs)

    def calculate(resolver=knowledge):
        return score_from_product(CARD, LINE, resolver, scorer=scorer)

    async def child():
        return calculate()

    with purchase_cost_knowledge_scope():
        calculate()
        calculate()
        assert len(calls) == 1
        await asyncio.create_task(child())
        assert len(calls) == 2
        calculate()
        assert len(calls) == 2
    with purchase_cost_knowledge_scope():
        calculate()
        assert len(calls) == 3
        calculate(KnowledgeResolver({"version": 2}))
        assert len(calls) == 4


@pytest.mark.asyncio
async def test_score_instrumentation_and_errors_never_reuse_another_scorer_result():
    knowledge = KnowledgeResolver()
    calls = []

    def first(*args, **kwargs):
        result = score_match(*args, **kwargs)
        result["reasons"].append("first scorer")
        return result

    def second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient calculation failure")
        result = score_match(*args, **kwargs)
        result["reasons"].append("second scorer")
        return result

    with purchase_cost_knowledge_scope():
        score_from_product(CARD, LINE, knowledge, scorer=first)
        with pytest.raises(RuntimeError):
            score_from_product(CARD, LINE, knowledge, scorer=second)
        result = score_from_product(CARD, LINE, knowledge, scorer=second)
        again = score_from_product(CARD, LINE, knowledge, scorer=second)
        assert "second scorer" in result["reasons"] and "first scorer" not in result["reasons"]
        assert again == result and len(calls) == 2


@pytest.mark.asyncio
async def test_changed_independent_product_words_are_rescored_and_never_authorize_cost():
    card = {k: v for k, v in CARD.items() if k not in {"serial_number", "serial_total"}}
    source = {**LINE, "title": "2025 Topps Chrome 卡片"}
    product = {
        "observations": {
            "single_card": True,
            "photos": [{"index": 0, "role": "front"}],
            "fields": [
                dict(kind=k, value=v, text=v, photo=0, origin="printed")
                for k, v in [
                    ("brand", "Topps"),
                    ("product", "Chrome"),
                    ("player", "Example Player"),
                ]
            ],
        }
    }
    knowledge = KnowledgeResolver()
    calls = []

    def scorer(*args, **kwargs):
        calls.append(1)
        return score_match(*args, **kwargs)

    with purchase_cost_knowledge_scope():
        first = score_from_product(card, source, knowledge, product, scorer=scorer)
        repeated = score_from_product(card, source, knowledge, product, scorer=scorer)
        assert first["recalled"] is True and first["automatic"] is False
        assert repeated == first and len(calls) == 2
        product["observations"]["fields"][-1]["value"] = "Other Player"
        changed = score_from_product(card, source, knowledge, product, scorer=scorer)
        assert changed["recalled"] is False and changed["automatic"] is False
        assert len(calls) == 3


@pytest.mark.asyncio
async def test_different_lines_prepare_same_card_once_and_changes_recompute(monkeypatch):
    knowledge = KnowledgeResolver()
    actual = knowledge.normalize_identity
    calls = []

    def normalize(card):
        calls.append(card)
        return actual(card)

    monkeypatch.setattr(knowledge, "normalize_identity", normalize)
    other_line = {**LINE, "title": LINE["title"] + " example suffix"}
    with purchase_cost_knowledge_scope():
        score_match(CARD, LINE, knowledge)
        score_match(CARD, other_line, knowledge)
        assert len(calls) == 1
        score_match({**CARD, "serial_number": 4}, LINE, knowledge)
        assert len(calls) == 2
        knowledge.version = "new-version"
        score_match(CARD, LINE, knowledge)
        assert len(calls) == 3
    with purchase_cost_knowledge_scope():
        score_match(CARD, LINE, knowledge)
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_player_aliases_reuse_only_same_player_mode_version_and_operation(monkeypatch):
    from card_platform.purchase_cost_bundle import purchase_player_aliases

    knowledge = KnowledgeResolver()
    actual = knowledge.aliases_for
    calls = []

    def aliases(*args, **kwargs):
        calls.append(args)
        return actual(*args, **kwargs)

    monkeypatch.setattr(knowledge, "aliases_for", aliases)
    with purchase_cost_knowledge_scope():
        first = purchase_player_aliases("Test Player", knowledge)
        count = len(calls)
        assert purchase_player_aliases("Test Player", knowledge) == first
        assert len(calls) == count
        purchase_player_aliases("Other Player", knowledge)
        assert len(calls) > count
        purchase_player_aliases("Test Player", knowledge, discovery=True)
        count = len(calls)
        purchase_player_aliases("Test Player", knowledge, discovery=True)
        assert len(calls) == count
        knowledge.version = "new-version"
        purchase_player_aliases("Test Player", knowledge)
        assert len(calls) > count
    count = len(calls)
    with purchase_cost_knowledge_scope():
        purchase_player_aliases("Test Player", knowledge)
    assert len(calls) > count


@pytest.mark.asyncio
async def test_prepared_identity_is_not_shared_mutable_state():
    from card_platform.purchase_cost import prepared_purchase_identity

    knowledge = KnowledgeResolver()
    with purchase_cost_knowledge_scope():
        first = prepared_purchase_identity(CARD, knowledge)
        first.conflicts.append("caller mutation")
        first.evidence["caller"] = [{"value": "mutation"}]
        second = prepared_purchase_identity(CARD, knowledge)
    assert "caller mutation" not in second.conflicts
    assert "caller" not in second.evidence


@pytest.mark.asyncio
async def test_prepared_matching_preserves_complete_uncached_results():
    lines = [
        LINE,
        {**LINE, "title": LINE["title"].replace("03/99", "04/99")},
        {**LINE, "title": "Other Player 2025 Topps Chrome card 21"},
    ]
    knowledge = KnowledgeResolver()
    baseline = [score_match(CARD, line, knowledge) for line in lines]
    with purchase_cost_knowledge_scope():
        assert [score_match(CARD, line, knowledge) for line in lines] == baseline


@pytest.mark.asyncio
async def test_child_task_does_not_reuse_prepared_parent_identity(monkeypatch):
    knowledge = KnowledgeResolver()
    actual = knowledge.normalize_identity
    calls = []

    def normalize(card):
        calls.append(card)
        return actual(card)

    monkeypatch.setattr(knowledge, "normalize_identity", normalize)

    async def child():
        score_match(CARD, LINE, knowledge)
        score_match(CARD, LINE, knowledge)

    with purchase_cost_knowledge_scope():
        score_match(CARD, LINE, knowledge)
        score_match(CARD, LINE, knowledge)
        await asyncio.create_task(child())
    assert len(calls) == 3
