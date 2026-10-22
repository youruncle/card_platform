import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _module(name="publish_knowledge_release"):
    path = Path(__file__).resolve().parents[3] / f"scripts/{name}.py"
    spec = importlib.util.spec_from_file_location("annual_publication", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_publication_preserves_prior_published_manual_and_draft_facts():
    merge = _module().preserve_unaffected_snapshot
    previous = {"version": 18, "entities": [
        {"id": "product", "attributes": {"old": True}},
        {"id": "draft", "attributes": {"published": True}},
        {"id": "manual", "attributes": {"published": True}},
        {"id": "retired"}, {"id": "other"}], "relations": []}
    current = {"version": 19, "entities": [
        {"id": "product", "attributes": {"new": True}},
        {"id": "manual", "attributes": {"unpublished_edit": True}},
        {"id": "unrelated_new_draft"}, {"id": "other"}], "relations": []}
    result = merge(previous, current, {"product", "retired"})
    facts = {e["id"]: e for e in result["entities"]}
    assert facts["product"]["attributes"] == {"new": True}
    assert facts["draft"]["attributes"] == {"published": True}
    assert facts["manual"]["attributes"] == {"published": True}
    assert "other" in facts
    assert "retired" not in facts and "unrelated_new_draft" not in facts
    assert result["version"] == 19 and previous["version"] == 18


def test_publication_drops_only_relations_to_explicitly_retired_facts():
    merge = _module().preserve_unaffected_snapshot
    previous = {"entities": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
                "relations": [{"id": 1, "subject_entity_id": "a", "object_entity_id": "b"},
                              {"id": 2, "subject_entity_id": "a", "object_entity_id": "c"}]}
    result = merge(previous, {"version": 19, "entities": [], "relations": []}, {"b"})
    assert [r["id"] for r in result["relations"]] == [2]


def _vector_fixture():
    assets = [{"asset_id": "a", "source_key": "source", "sha256": "imagehash",
               "visibility": "public_reference", "status": "ready"}]
    records = [{"asset_id": "a", "modality": mode, "sha256": "imagehash", "source_key": "source"}
               for mode in ("image", "text")]
    index = SimpleNamespace(records=records, knowledge_version="batch", model=SimpleNamespace(fingerprint="model"))
    report = {"assets_sha256": "currentjsonl", "knowledge_version": "batch", "model_fingerprint": "model"}
    return assets, index, report


def test_release_rejects_changed_caption_or_side_via_exact_asset_snapshot_hash():
    validate = _module("prepare_knowledge_release").validate_vector_generation
    assets, index, report = _vector_fixture()
    validate(assets, index, report, "currentjsonl")
    with pytest.raises(ValueError, match="current media"):
        validate(assets, index, report, "jsonlwithchangedcaption")


def test_release_rejects_missing_text_vector_or_duplicate_modality():
    validate = _module("prepare_knowledge_release").validate_vector_generation
    assets, index, report = _vector_fixture()
    index.records.pop()
    with pytest.raises(ValueError, match="one image and one text"):
        validate(assets, index, report, "currentjsonl")
    index.records.append(dict(index.records[0]))
    with pytest.raises(ValueError, match="one image and one text"):
        validate(assets, index, report, "currentjsonl")
