"""Offline contract fixtures; failures catch accepting unverifiable identities or input writes."""
import gzip
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from card_platform.knowledge import snapshot_checksum


def save(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    products = []
    for key, name, end in [("donruss", "Panini Donruss", 2024),
                           ("optic", "Panini Donruss Optic", 2025)]:
        products.append({"id": key, "entity_type": "product", "canonical_name": name,
                         "attributes": {"source_key": key, "manufacturer": "Panini",
                                        "product": name, "sport": "Basketball",
                                        "season_start": 2024, "season_end": end,
                                        "source_url": "https://support.paniniamerica.net/" + key}})
    card = {"id": "official:donruss:1", "entity_type": "card", "canonical_name": "A",
            "attributes": {"source_key": "donruss", "product": "Panini Donruss",
                           "section": "Base", "card_number": "1", "player": "A",
                           "source_checksum": "a" * 64, "source_page": 1, "source_line": 2,
                           "source_locators": [{"source_page": 1, "source_line": 2,
                                                "raw_text": "1 A"}]}}
    data = gzip.compress(json.dumps([card]).encode(), mtime=0)
    (root / "cards.gz").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    snapshot = {"entities": products, "relations": [], "official_artifacts": [
        {"source_key": "donruss", "checksum": digest, "card_count": 1, "path": "cards.gz"},
        {"source_key": "optic", "checksum": "f" * 64, "card_count": 2, "path": "missing.gz"},
    ]}
    baseline = {"contract": "knowledge-readonly-baseline-v1", "captured_at": "2026-10-27T01:00:00Z",
                "workspace_revision": None, "published": {"version": 1, "version_id": "v1",
                "checksum": snapshot_checksum(snapshot)}, "snapshot": snapshot,
                "artifact_files": [{"checksum": digest, "path": "cards.gz"}],
                "coverage_registry": {"status": "available", "rows": []}}
    scope = {"contract": "knowledge-intake-plan-scope-v1", "season_starts": [2023, 2024, 2025],
             "existing_product_keys": ["donruss", "optic"], "annual_research_keys": ["Panini:2024"]}
    save(root, "baseline.json", baseline)
    save(root, "scope.json", scope)
    save(root, "captures.json", {"contract": "knowledge-source-inputs-v1", "items": []})
    return baseline, scope


def load(root):
    from card_platform.knowledge_source_inputs import load_inputs
    return load_inputs(root, Path("baseline.json"), Path("scope.json"), Path("captures.json"))


def test_full_snapshot_checksum_and_exact_scope(tmp_path):
    from card_platform.knowledge_source_inputs import InputContractError
    baseline, scope = fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    inputs = load(tmp_path)
    assert inputs["scope"]["existing_product_keys"] == ["donruss", "optic"]
    assert inputs["baseline"]["snapshot"]["entities"][0]["attributes"]["season_end"] == 2024
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    baseline["snapshot"]["entities"][0]["attributes"]["season_end"] = 2025
    save(tmp_path, "baseline.json", baseline)
    with pytest.raises(InputContractError, match="checksum"):
        load(tmp_path)
    fixture(tmp_path)
    for keys in [["missing"], ["donruss", "donruss"]]:
        save(tmp_path, "scope.json", {**scope, "existing_product_keys": keys})
        with pytest.raises(InputContractError):
            load(tmp_path)


def test_unavailable_artifact_is_visible(tmp_path):
    fixture(tmp_path)
    inputs = load(tmp_path)
    assert len(inputs["baseline_cards"]["donruss"]) == 1
    assert "optic" not in inputs["baseline_cards"]
    assert inputs["baseline_artifact_issues"][0]["code"] == "missing_artifact"
    (tmp_path / "cards.gz").write_bytes(b"tampered")
    inputs = load(tmp_path)
    assert inputs["baseline_cards"] == {}
    assert {i["code"] for i in inputs["baseline_artifact_issues"]} == {
        "missing_artifact", "artifact_checksum_mismatch"}


def test_out_of_scope_rows_are_verified_and_counted_without_retention(tmp_path):
    from card_platform.knowledge_coverage_report import build_coverage
    from card_platform.knowledge_intake_plan import build_plan

    _, scope = fixture(tmp_path)
    save(tmp_path, "scope.json", {**scope, "existing_product_keys": ["optic"]})
    inputs = load(tmp_path)
    assert "donruss" not in inputs["baseline_cards"]
    report = build_coverage(inputs, build_plan(inputs, []))
    assert report["known_baseline"]["observed_verified_checklist_records"] == 1
    assert report["known_baseline"]["missing_artifact_count"] == 1
    assert report["work_scope"]["observed_verified_checklist_records"] == 0
    (tmp_path / "cards.gz").write_bytes(b"tampered")
    damaged = load(tmp_path)
    report = build_coverage(damaged, build_plan(damaged, []))
    assert report["known_baseline"]["observed_verified_checklist_records"] == 0
    assert report["known_baseline"]["missing_artifact_count"] == 2


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_json_fails_closed(tmp_path, raw):
    from card_platform.knowledge_source_inputs import InputContractError
    fixture(tmp_path)
    (tmp_path / "scope.json").write_text(raw)
    with pytest.raises(InputContractError):
        load(tmp_path)


def test_paths_and_json_fail_closed(tmp_path):
    from card_platform.knowledge_source_inputs import (
        InputContractError,
        load_inputs,
        validate_output_paths,
    )
    root = tmp_path / "input"
    fixture(root)
    outside = save(tmp_path, "outside.json", {})
    (root / "escape.json").symlink_to(outside)
    for bad in [Path("../outside.json"), Path("escape.json"), outside]:
        with pytest.raises(InputContractError):
            load_inputs(root, bad, Path("scope.json"), Path("captures.json"))
    for output, cache in [(root, tmp_path / "cache"), (tmp_path, tmp_path / "cache"),
                          (root / "out", tmp_path / "cache"),
                          (tmp_path / "out", tmp_path / "out/cache")]:
        with pytest.raises(InputContractError):
            validate_output_paths(root, output, cache)
    validate_output_paths(root, tmp_path / "out", tmp_path / "cache")


def test_missing_optional_metadata_is_unknown(tmp_path):
    baseline, _ = fixture(tmp_path)
    baseline.pop("coverage_registry")
    save(tmp_path, "baseline.json", baseline)
    inputs = load(tmp_path)
    assert inputs["baseline"]["coverage_registry"] == {"status": "unavailable", "rows": []}
    assert inputs["baseline"].get("sources") is None


def test_artifact_count_scope_and_duplicate_descriptors_fail_conservatively(tmp_path):
    baseline, _ = fixture(tmp_path)
    bad = deepcopy(baseline["snapshot"]["official_artifacts"][0])
    bad["card_count"] = 99
    baseline["snapshot"]["official_artifacts"] = [bad]
    baseline["published"]["checksum"] = snapshot_checksum(baseline["snapshot"])
    save(tmp_path, "baseline.json", baseline)
    inputs = load(tmp_path)
    assert not inputs["baseline_cards"]
    assert inputs["baseline_artifact_issues"][0]["code"] == "artifact_shape_mismatch"
    original = {**bad, "card_count": 1}
    baseline["snapshot"]["official_artifacts"] = [original, bad]
    baseline["published"]["checksum"] = snapshot_checksum(baseline["snapshot"])
    save(tmp_path, "baseline.json", baseline)
    assert not load(tmp_path)["baseline_cards"]


def official(root, *, input_id="official", key="optic", player="B"):
    data = ("SPORT,CARD SET,CARD NUMBER,ATHLETE,TEAM,SEQUENCE\n"
            f"Basketball,Base,2,{player},Team B,25\n"
            "Basketball,Base,3,C,Team C,\n").encode()
    path = root / f"{input_id}.csv"
    path.write_bytes(data)
    product = {"source_key": key, "title": "2024-25 Panini Donruss Optic Basketball",
               "publisher": "Panini", "source_url": "https://support.paniniamerica.net/get-product-checklist?program=Optic",
               "season_start": 2024, "season_end": 2025}
    receipt = {**product, "status": "downloaded", "http_status": 200,
               "captured_at": "2026-10-27T00:00:00Z", "path": path.name,
               "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    return {"input_id": input_id, "product_source_key": key,
            "adapter": "official_document_v1", "product": product, "receipt": receipt}


def specialist(root, *, locator=True):
    raw = root / "specialist.html"
    raw.write_text("<p>Print run is 25.</p>")
    record = {"source_key": "donruss", "page_url": "https://example.test/checklist?q=1#fact",
              "site": "Example", "source_authority": "specialist", "limits": ["Not official"],
              "evidence": {"capture_path": raw.name,
                           "capture_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                           "capture_kind": "original_html"},
              "facts": [{"field": "print_run", "value": 25,
                         **({"source_locator": "p: Print run is 25."} if locator else {})}]}
    save(root, "facts.json", {"captured_at": "2026-10-27T00:00:00Z", "products": [record]})
    return {"input_id": "specialist", "product_source_key": "donruss",
            "adapter": "specialist_facts_v1", "facts_path": "facts.json"}


def set_captures(root, items):
    save(root, "captures.json", {"contract": "knowledge-source-inputs-v1", "items": items})
    return load(root)


def test_official_adapter_reuses_compiler(tmp_path):
    from card_platform.knowledge_source_inputs import extract_sources
    root = tmp_path / "input"
    fixture(root)
    item = official(root)
    result = extract_sources(set_captures(root, [item]), tmp_path / "cache")[0]
    assert result["status"] == "ready"
    assert [c["attributes"]["card_number"] for c in result["cards"]] == ["2", "3"]
    assert result["cards"][0]["attributes"]["serial_total"] == 25
    assert result["cards"][0]["attributes"]["source_locators"][0]["source_line"] == 2
    item["receipt"]["sha256"] = "0" * 64
    blocked = extract_sources(set_captures(root, [item]), tmp_path / "cache")[0]
    assert blocked["status"] == "blocked"
    assert blocked["issues"][0]["code"] == "capture_checksum_mismatch"
    item = official(root)
    item["product"]["season_end"] = 2024
    assert extract_sources(set_captures(root, [item]), tmp_path / "cache")[0]["status"] == "blocked"


def test_specialist_facts_preserve_provenance(tmp_path):
    from card_platform.knowledge_source_inputs import extract_sources
    root = tmp_path / "input"
    fixture(root)
    item = specialist(root)
    result = extract_sources(set_captures(root, [item]), tmp_path / "cache")[0]
    fact = result["facts"][0]
    assert fact["value"] == 25
    assert fact["locator"] == "p: Print run is 25."
    assert fact["json_pointer"] == "/products/0/facts/0"
    assert fact["authority"] == "specialist"
    assert fact["capture_kind"] == "original_html"
    assert fact["limits"] == ["Not official"]
    result = extract_sources(set_captures(root, [specialist(root, locator=False)]), tmp_path / "cache")[0]
    assert result["facts"][0]["review_reason"] == "missing_source_locator"
    item["adapter"] = "magic_html"
    assert extract_sources(set_captures(root, [item]), tmp_path / "cache")[0]["issues"][0]["code"] == "unsupported_adapter"


def test_cache_reuse_invalidation_and_corruption(tmp_path, monkeypatch):
    from card_platform import knowledge_source_inputs as module
    root = tmp_path / "input"
    fixture(root)
    first, second = official(root), official(root, input_id="other", player="D")
    inputs = set_captures(root, [first, second])
    calls = []
    real = module.compile_source
    def spy(*args, **kwargs):
        calls.append(args[0]["source_key"])
        return real(*args, **kwargs)
    monkeypatch.setattr(module, "compile_source", spy)
    cache = tmp_path / "cache"
    initial = module.extract_sources(inputs, cache)
    assert len(calls) == 2
    calls.clear()
    assert module.extract_sources(load(root), cache) == initial
    assert calls == []
    changed = official(root, player="Changed")
    module.extract_sources(set_captures(root, [changed, second]), cache)
    assert len(calls) == 1
    for name in ["PARSER_VERSION", "COMPILER_VERSION"]:
        calls.clear()
        monkeypatch.setattr(module, name, getattr(module, name) + "-new")
        module.extract_sources(load(root), cache)
        assert len(calls) == 2
    calls.clear()
    current = load(root)
    current["items"][0]["product"]["directory_url"] = "https://paniniamerica.net/updated"
    module.extract_sources(current, cache)
    assert len(calls) == 1
    calls.clear()
    for path in (cache / "entries").glob("*.json"):
        value = json.loads(path.read_text())
        value["payload"]["cards"] = []
        path.write_text(json.dumps(value))
    repaired = module.extract_sources(load(root), cache)
    assert len(calls) == 2
    assert len(repaired[0]["cards"]) == 2


def test_403_missing_capture_and_symlink_are_not_usable(tmp_path):
    from card_platform.knowledge_source_inputs import InputContractError, extract_sources
    root = tmp_path / "input"
    fixture(root)
    item = official(root)
    item["receipt"]["http_status"] = 403
    result = extract_sources(set_captures(root, [item]), tmp_path / "cache")[0]
    assert result["issues"][0]["code"] == "capture_http_error"
    item = official(root)
    (root / item["receipt"]["path"]).unlink()
    result = extract_sources(set_captures(root, [item]), tmp_path / "cache")[0]
    assert result["issues"][0]["code"] == "missing_capture"
    item = specialist(root)
    facts = json.loads((root / "facts.json").read_text())
    facts["products"][0]["evidence"]["capture_path"] = "../outside"
    save(root, "facts.json", facts)
    with pytest.raises(InputContractError):
        extract_sources(set_captures(root, [item]), tmp_path / "cache")


def test_blocked_extraction_is_retried_and_not_frozen_by_cache(tmp_path, monkeypatch):
    from card_platform import knowledge_source_inputs as module
    root = tmp_path / "input"
    fixture(root)
    inputs = set_captures(root, [official(root)])
    real = module.compile_source
    def unavailable(*args, **kwargs):
        raise ImportError("missing deterministic parser dependency")
    monkeypatch.setattr(module, "compile_source", unavailable)
    assert module.extract_sources(inputs, tmp_path / "cache")[0]["status"] == "blocked"
    monkeypatch.setattr(module, "compile_source", real)
    assert module.extract_sources(inputs, tmp_path / "cache")[0]["status"] == "ready"


def test_utf8_only_and_malformed_shapes_fail_as_contract_errors(tmp_path):
    from card_platform.knowledge_source_inputs import InputContractError
    fixture(tmp_path)
    data = (tmp_path / "scope.json").read_text()
    (tmp_path / "scope.json").write_bytes(data.encode("utf-16"))
    with pytest.raises(InputContractError):
        load(tmp_path)
    fixture(tmp_path)
    save(tmp_path, "captures.json", {"contract": "knowledge-source-inputs-v1", "items": [
        {"input_id": "bad", "product_source_key": "donruss", "adapter": "official_document_v1", "receipt": None}]})
    with pytest.raises(InputContractError):
        load(tmp_path)


def test_actual_compiler_quarantined_rows_preserved_for_review(tmp_path):
    from card_platform.knowledge_intake_plan import build_plan
    from card_platform.knowledge_source_inputs import extract_sources
    root = tmp_path / "input"
    fixture(root)
    item = official(root)
    path = root / item["receipt"]["path"]
    path.write_text("SPORT,CARD SET,CARD NUMBER,ATHLETE,TEAM,SEQUENCE\n"
                    "Basketball,Base,2,B,Team B,25\nBasketball,Base,2,B,Team B,99\n")
    item["receipt"].update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size)
    inputs = set_captures(root, [item])
    extracted = extract_sources(inputs, tmp_path / "cache")
    assert len(extracted[0]["cards"]) == 2
    decisions = build_plan(inputs, extracted)["fact_decisions"]
    serials = [d for d in decisions if d["field"] == "serial_total"]
    assert {d["value"] for d in serials} == {25, 99}
    assert all(d["status"] == "review_needed" for d in serials)


@pytest.mark.parametrize("sport", ["football", "WNBA"])
def test_official_compiler_cannot_rebind_frozen_sport(tmp_path, sport):
    from card_platform.knowledge_source_inputs import extract_sources
    root = tmp_path / "input"
    baseline, _ = fixture(root)
    baseline["snapshot"]["entities"][1]["attributes"]["sport"] = sport
    baseline["published"]["checksum"] = snapshot_checksum(baseline["snapshot"])
    save(root, "baseline.json", baseline)
    result = extract_sources(set_captures(root, [official(root)]), tmp_path / "cache")[0]
    assert result["status"] == "blocked"
    assert result["cards"] == []
    assert result["issues"][0]["code"] == "capture_product_mismatch"


def test_supplied_capture_sport_must_also_match_frozen_scope(tmp_path):
    from card_platform.knowledge_source_inputs import extract_sources
    root = tmp_path / "input"
    fixture(root)
    item = official(root)
    item["product"]["sport"] = "football"
    assert extract_sources(set_captures(root, [item]), tmp_path / "cache")[0]["status"] == "blocked"
