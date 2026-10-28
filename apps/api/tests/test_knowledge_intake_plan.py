from copy import deepcopy

from test_knowledge_source_inputs import fixture, load, save, set_captures, specialist

from card_platform.knowledge import snapshot_checksum


def extraction(*, value=25, url="https://example.test/a", digest="a" * 64,
               subject=None, field="print_run", key="donruss", input_id="s"):
    return {"input_id": input_id, "product_source_key": key,
            "source_url": url, "source_sha256": digest,
            "adapter": "specialist_facts_v1", "adapter_version": "specialist-adapter-v1",
            "status": "ready", "cards": [], "issues": [], "facts": [
                {"field": field, "value": value, "locator": "paragraph 1",
                 **({"subject_key": subject} if subject is not None else {})}]}


def evidence(*, value=25, url="https://example.test/a", digest="a" * 64, subject=None):
    return {"source_url": url, "source_sha256": digest,
            "facts": [{"field": "print_run", "value": value, "source_locator": "paragraph 1",
                       **({"subject_key": subject} if subject else {})}]}


def baseline_with(root, records):
    baseline, _ = fixture(root)
    baseline["snapshot"]["entities"][0]["attributes"]["supplementary_evidence"] = records
    baseline["published"]["checksum"] = snapshot_checksum(baseline["snapshot"])
    save(root, "baseline.json", baseline)
    return load(root)


def test_exact_fact_duplicate_and_new_provenance(tmp_path):
    from card_platform.knowledge_intake_plan import build_plan
    inputs = baseline_with(tmp_path, [evidence(value=99, url="https://other.test/unused"), evidence()])
    before = deepcopy(inputs["baseline"])
    plan = build_plan(inputs, [extraction()])
    assert plan["fact_decisions"][0]["status"] == "duplicate"
    assert inputs["baseline"] == before
    inputs = baseline_with(tmp_path, [evidence()])
    plan = build_plan(inputs, [extraction(url="https://new.test/a", digest="b" * 64)])
    decision = plan["fact_decisions"][0]
    assert decision["status"] == "corroborating"
    assert decision["baseline_evidence"][0]["source_url"] == "https://example.test/a"
    incomplete = evidence()
    incomplete.pop("source_sha256")
    plan = build_plan(baseline_with(tmp_path, [incomplete]), [extraction()])
    assert plan["fact_decisions"][0]["status"] == "review_needed"
    assert "baseline_provenance_incomplete" in plan["fact_decisions"][0]["reasons"]


def test_conflict_is_subject_scoped_and_preserves_values(tmp_path):
    from card_platform.knowledge_intake_plan import build_plan
    inputs = baseline_with(tmp_path, [evidence(value=25, subject="card:known")])
    decisions = build_plan(inputs, [extraction(value=99, subject="card:known")])["fact_decisions"]
    assert decisions[0]["reasons"] == ["conflict"]
    assert decisions[0]["value"] == 99
    assert decisions[0]["baseline_evidence"][0]["value"] == 25
    assert build_plan(inputs, [extraction(value=99, subject="card:other")])["fact_decisions"][0]["status"] == "new_candidate"
    inputs = baseline_with(tmp_path, [evidence()])
    assert build_plan(inputs, [extraction(value="25")])["fact_decisions"][0]["status"] == "review_needed"
    assert build_plan(inputs, [extraction(key="optic")])["fact_decisions"][0]["status"] == "new_candidate"
    a = extraction(value={"name": "Gold", "print": 10}, field="parallel_rule")
    b = extraction(value={"name": "Red", "print": 25}, field="parallel_rule", input_id="b")
    rows = build_plan(inputs, [a, b])["fact_decisions"]
    assert len(rows) == 2
    assert all(r["reasons"] == ["coarse_product_claim_requires_review"] for r in rows)


def test_product_fact_plan_does_not_expand_unrequested_card_facts(tmp_path, monkeypatch):
    from card_platform import knowledge_intake_plan as planner

    fixture(tmp_path)
    inputs = load(tmp_path)
    original = planner._card_candidates
    expanded = []

    def track(source, cards):
        expanded.extend(cards)
        return original(source, cards)

    monkeypatch.setattr(planner, "_card_candidates", track)
    result = planner.build_plan(inputs, [extraction()])
    assert result["fact_decisions"][0]["status"] == "new_candidate"
    assert expanded == []


def card(serial, *, player="A / B", marker=None):
    return {"entity_type": "card", "attributes": {
        "source_key": "optic", "section": "Base", "card_number": "1", "player": player,
        "serial_total": serial, "source_variant_label": marker,
        "source_locators": [{"source_page": 1, "source_line": 1, "raw_text": "1 A / B"}]}}


def official_extraction(cards):
    return {**extraction(key="optic"), "adapter": "official_document_v1", "cards": cards, "facts": []}


def test_duplicate_official_row_keys_never_hide_disagreement(tmp_path):
    from card_platform.knowledge_intake_plan import build_plan
    fixture(tmp_path)
    inputs = load(tmp_path)
    rows = build_plan(inputs, [official_extraction([card(25), card(99)])])
    serial = [r for r in rows["fact_decisions"] if r["field"] == "serial_total"]
    assert len(serial) == 2
    assert {r["value"] for r in serial} == {25, 99}
    assert all("conflicting_official_row" in r["reasons"] for r in serial)
    assert rows["source_actions"][0]["semantic_delta"] is None
    inputs["baseline_cards"]["optic"] = [card(25, player="B / A")]
    rows = build_plan(inputs, [official_extraction([card(25)])])
    assert rows["source_actions"][0]["semantic_delta"] == {"new": 0, "changed": 0, "unchanged": 1, "removed": 0}
    assert rows["source_actions"][0]["source_representation_delta"]["new"] == 1
    assert next(r for r in rows["fact_decisions"] if r["field"] == "player")["value"] == "A / B"
    assert all(r["base_card_id"] is None and r["physical_card_id"] is None for r in rows["fact_decisions"])


def test_baseline_change_recomputes_delta_without_reextracting(tmp_path, monkeypatch):
    from card_platform.knowledge_intake_plan import build_plan
    from card_platform.knowledge_source_inputs import extract_sources
    root = tmp_path / "input"
    baseline, _ = fixture(root)
    item = specialist(root)
    inputs = set_captures(root, [item])
    extracted = extract_sources(inputs, tmp_path / "cache")
    first = build_plan(inputs, extracted)
    assert first["fact_decisions"][0]["status"] == "new_candidate"
    record = evidence(url=extracted[0]["source_url"], digest=extracted[0]["source_sha256"])
    baseline["snapshot"]["entities"][0]["attributes"]["supplementary_evidence"] = [record]
    baseline["published"]["checksum"] = snapshot_checksum(baseline["snapshot"])
    save(root, "baseline.json", baseline)
    current = load(root)
    extracted2 = extract_sources(current, tmp_path / "cache")
    second = build_plan(current, extracted2)
    assert second["fact_decisions"][0]["status"] == "duplicate"
    assert first["fact_decisions"][0]["candidate_id"] == second["fact_decisions"][0]["candidate_id"]
    assert second["publishable"] is False and second["llm_calls"] == 0


def test_stable_candidate_identity_keeps_query_types_and_all_locators(tmp_path):
    from card_platform.knowledge_intake_plan import build_plan, candidate_key
    fixture(tmp_path)
    inputs = load(tmp_path)
    a = extraction(url="HTTPS://EXAMPLE.test/a?q=1#one")
    b = extraction(url="https://example.test/a?q=1#two", input_id="b")
    b["facts"][0]["locator"] = "paragraph 2"
    plan = build_plan(inputs, [a, b])
    assert plan == build_plan(inputs, [b, a])
    assert len(plan["fact_decisions"]) == 1
    row = plan["fact_decisions"][0]
    assert row["locators"] == ["paragraph 1", "paragraph 2"]
    assert candidate_key(row) != candidate_key({**row, "value": "25"})
    assert candidate_key(row) != candidate_key({**row, "source_url": "https://example.test/a?q=2"})
    assert candidate_key(row) == candidate_key({**row, "value": 25})


def test_missing_baseline_source_provenance_not_labeled_new(tmp_path):
    from card_platform.knowledge_intake_plan import build_plan
    fixture(tmp_path)
    plan = build_plan(load(tmp_path), [official_extraction([card(25)])])
    assert plan["source_actions"][0]["status"] == "blocked"
    assert "baseline_source_provenance_incomplete" in plan["source_actions"][0]["reasons"]
    assert "owning_source_rows" in plan["focused_v2_required_inputs"]


def test_legacy_unattributed_value_and_unknown_artifact_do_not_become_new(tmp_path):
    from card_platform.knowledge_intake_plan import build_plan
    fixture(tmp_path)
    inputs = load(tmp_path)
    inputs["products"]["donruss"]["attributes"]["print_run"] = 25
    decision = build_plan(inputs, [extraction()])["fact_decisions"][0]
    assert decision["status"] == "review_needed"
    assert "baseline_provenance_incomplete" in decision["reasons"]
    rows = build_plan(inputs, [official_extraction([card(25)])])["fact_decisions"]
    assert all("baseline_artifact_unavailable" in r["reasons"] for r in rows)


def test_dedup_keeps_all_fact_json_pointers_and_frozen_product_binding(tmp_path):
    from card_platform.knowledge_intake_plan import build_plan
    fixture(tmp_path)
    a, b = extraction(), extraction(input_id="b")
    a["facts"][0]["json_pointer"] = "/products/0/facts/0"
    b["facts"][0]["json_pointer"] = "/products/0/facts/1"
    plan = build_plan(load(tmp_path), [a, b])
    row = plan["fact_decisions"][0]
    assert {p["json_pointer"] for p in row["provenance"]} == {"/products/0/facts/0", "/products/0/facts/1"}
    assert plan["work_scope"]["frozen_products"][0]["season_end"] == 2024
