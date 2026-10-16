from copy import deepcopy
import pytest
from card_platform.knowledge_card_catalog import ScopedCardCatalog

ROW = {
    "id": "r5",
    "entity_type": "card",
    "attributes": {
        "product": "Topps Signature Class",
        "brand": "Topps",
        "season_start": 2025,
        "season_end": 2026,
        "card_number": "R-5",
        "player": "Tracy McGrady",
        "insert": "Roses",
        "source_url": "https://www.topps.com/checklist.pdf",
        "source_locator": "page 10 line 12",
    },
}
TARGET = dict(
    product_name="Signature Class",
    brand_name="Topps",
    season_start=2026,
    season_end=None,
    card_number="R-5",
    player_name="Tracy McGrady",
)


def test_scoped_unique_missing_field_with_provenance_and_no_mutation():
    original = deepcopy(TARGET)
    result, evidence = ScopedCardCatalog([ROW], version=9).enrich(TARGET)
    assert result["insert_name"] == "Roses"
    assert evidence[0]["knowledge_version"] == 9
    assert evidence[0]["source_url"] == ROW["attributes"]["source_url"]
    assert TARGET == original
    assert "parallel_name" not in result and "serial_total" not in result


@pytest.mark.parametrize(
    "changes",
    [
        {"product_name": "Chrome"},
        {"brand_name": "Panini"},
        {"card_number": "R-6"},
        {"player_name": "Derrick Rose"},
        {"season_start": 2024},
        {"season_start": 2026, "season_end": 2027},
        {"player_name": None},
        {"season_start": None},
    ],
)
def test_no_cross_scope_guess(changes):
    result, evidence = ScopedCardCatalog([ROW]).enrich({**TARGET, **changes})
    assert not evidence and not result.get("insert_name")


def test_conflicting_inserts_not_filled_and_existing_values_preserved():
    other = {**ROW, "id": "other", "attributes": {**ROW["attributes"], "insert": "Other"}}
    result, evidence = ScopedCardCatalog([ROW, other]).enrich(TARGET)
    assert not result.get("insert_name") and not evidence
    result, evidence = ScopedCardCatalog([ROW]).enrich({**TARGET, "insert_name": "Confirmed"})
    assert result["insert_name"] == "Confirmed" and not evidence


def test_common_insert_across_parallel_versions_does_not_infer_parallel():
    red = {
        **ROW,
        "id": "red",
        "attributes": {**ROW["attributes"], "parallel": "Red", "serial_total": 25},
    }
    result, evidence = ScopedCardCatalog([ROW, red]).enrich(TARGET)
    assert result["insert_name"] == "Roses"
    assert not result.get("parallel_name") and not result.get("serial_total")


def test_existing_parallel_filters_conflicting_insert():
    other = {
        **ROW,
        "id": "red",
        "attributes": {**ROW["attributes"], "parallel": "Red", "insert": "Other"},
    }
    result, _ = ScopedCardCatalog([ROW, other]).enrich({**TARGET, "parallel_name": "Blue"})
    assert result.get("insert_name") == "Roses"


def test_published_facts_reach_query_plan_without_product_specific_fallback():
    from card_platform.knowledge import KnowledgeResolver

    row = deepcopy(ROW)
    row["canonical_name"] = "Official test card"
    row["attributes"]["product"] = "Novel Product"
    identity = {**TARGET, "product_name": "Novel Product"}
    resolver = KnowledgeResolver({"version": 19, "entities": [row]})
    normalized, changes = resolver.normalize_identity(identity)
    assert normalized["insert_name"] == "Roses"
    assert changes[0]["method"] == "scoped_official_checklist"
    stages = resolver.build_query_plan(identity)
    assert any("Roses" in str(stage) for stage in stages)


def test_valuation_worker_consumes_published_scoped_fact():
    from types import SimpleNamespace
    from card_platform.knowledge import KnowledgeResolver
    from card_platform.valuation_worker import AutomatedValuationWorker

    row = deepcopy(ROW)
    row["canonical_name"] = "Test card"
    row["attributes"]["product"] = "Novel Product"
    worker = object.__new__(AutomatedValuationWorker)
    worker.knowledge_runtime = SimpleNamespace(
        resolver=KnowledgeResolver({"version": 19, "entities": [row]})
    )
    identity = worker.resolve_identity_row({**TARGET, "product_name": "Novel Product"})
    assert identity["insert_name"] == "Roses"
    assert identity["knowledge_identity_evidence"][0]["knowledge_version"] == 19
