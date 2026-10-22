import importlib.util
from pathlib import Path

import pytest


def test_supplement_withholds_unknown_or_ambiguous_season_status():
    path = Path(__file__).resolve().parents[1] / "supplement_knowledge_sources.py"
    spec = importlib.util.spec_from_file_location("supplement_sources", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for status in ("scope_ambiguous_season", "season_scope_ambiguous", "pending", "verified", None):
        assert not module.identity_is_publishable({"status": status})
    assert not module.identity_is_publishable(None)
    for status in ("unmatched_official_card", "core_identity_matched_variant_unresolved", "exact_official_section_matched"):
        assert module.identity_is_publishable({"status": status})


def test_unmatched_official_scan_still_requires_source_and_image_review_binding():
    path = Path(__file__).resolve().parents[1] / "supplement_knowledge_sources.py"
    spec = importlib.util.spec_from_file_location("supplement_sources_binding", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    record = {"source_key": "a", "page_url": "https://source.test/cert/1", "source_artifact_sha256": "abc",
              "image_urls_observed": [{"side": "front", "sha256": "image"}]}
    review = {"status": "unmatched_official_card", "source_key": "a",
              "source_identity": {"source_page_url": record["page_url"], "source_artifact_sha256": "abc"},
              "image_evidence": [{"side": "front", "sha256": "image"}]}
    module.validate_scan_binding(record, review)
    with pytest.raises(ValueError, match="review source"):
        module.validate_scan_binding(record | {"source_key": "b"}, review)
    with pytest.raises(ValueError, match="absent from identity review"):
        module.validate_scan_binding(record | {"image_urls_observed": [{"side": "back", "sha256": "image"}]}, review)
