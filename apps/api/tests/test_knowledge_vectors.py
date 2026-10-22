"""Portable retrieval contracts; tiny vectors are hand-derived, not a model quality claim."""

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from card_platform.knowledge_vectors import (
    ModelSpec,
    SiglipEncoder,
    VectorIndex,
    build_asset_index,
    search_image,
    search_text,
)


SPEC = ModelSpec("test/joint", "immutable-revision", 3, "rgb-test-v1", "joint-test")


def record(asset_id, modality="image", side="front", **extra):
    return {
        "vector_id": f"{asset_id}:{modality}", "asset_id": asset_id,
        "modality": modality, "side": side, "access_scope": "public_reference",
        "source_url": "https://example.org/checklist", "image_url": "https://example.org/card.jpg",
        "source_sha256": "a" * 64, "season_start": 2025,
        **extra,
    }


def test_persisted_cosine_ranking_and_source_links(tmp_path):
    # Raw dot products would incorrectly put b first; cosine gives a then c.
    index = VectorIndex(SPEC, [record("a"), record("b"), record("c")],
                        [[1, 0, 0], [100, 100, 0], [4, 3, 0]], "batch-v1")
    index.save(tmp_path)
    loaded = VectorIndex.load(tmp_path, expected_model=SPEC)
    hits = loaded.search([5, 0, 0], model=SPEC, top_k=2)
    assert [hit["asset_id"] for hit in hits] == ["a", "c"]
    assert hits[0]["score"] == pytest.approx(1)
    assert hits[1]["score"] == pytest.approx(0.8)
    assert hits[0]["source_url"] == "https://example.org/checklist"
    assert hits[0]["knowledge_version"] == "batch-v1"


def test_private_vectors_require_matching_account_and_sides_stay_separate(tmp_path):
    index = VectorIndex(SPEC, [record("front"), record("back", side="back"),
                              record("private", access_scope="private", owner_id="alice"),
                              record("caption", modality="text", side="unknown")],
                        [[1, 0, 0]] * 4, "v1")
    index.save(tmp_path)
    loaded = VectorIndex.load(tmp_path)
    assert {x["asset_id"] for x in loaded.search([1, 0, 0], model=SPEC)} == {
        "front", "back", "caption"}
    assert {x["asset_id"] for x in loaded.search([1, 0, 0], model=SPEC, owner_id="bob")} == {
        "front", "back", "caption"}
    assert {x["asset_id"] for x in loaded.search([1, 0, 0], model=SPEC, owner_id="alice")} == {
        "front", "back", "caption", "private"}
    assert [x["asset_id"] for x in loaded.search([1, 0, 0], model=SPEC,
                                               modality="image", side="back")] == ["back"]
    assert loaded.search([1, 0, 0], model=SPEC, filters={"season_start": 2024}) == []


@pytest.mark.parametrize("change", [
    {"revision": "other"}, {"dimension": 4}, {"preprocessing": "other"},
    {"embedding_space": "other"}, {"model_id": "other"},
])
def test_rejects_model_space_changes(tmp_path, change):
    index = VectorIndex(SPEC, [record("a")], [[1, 0, 0]], "v1")
    index.save(tmp_path)
    other = replace(SPEC, **change)
    with pytest.raises(ValueError, match="model"):
        VectorIndex.load(tmp_path, expected_model=other)
    with pytest.raises(ValueError, match="model"):
        index.search([1, 0, 0], model=other)


@pytest.mark.parametrize("vectors", [[[0, 0, 0]], [[float("nan"), 0, 0]], [[1, 0]]])
def test_invalid_vectors_do_not_become_candidates(vectors):
    with pytest.raises(ValueError):
        VectorIndex(SPEC, [record("bad")], vectors, "v1")


def test_integrity_and_no_object_array_loading(tmp_path):
    VectorIndex(SPEC, [record("a")], [[1, 0, 0]], "v1").save(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    vector_path = tmp_path / manifest["vectors_file"]
    with vector_path.open("wb") as stream:
        np.save(stream, np.array([{"unsafe": "object"}], dtype=object), allow_pickle=True)
    # Even if a tampered checksum is supplied, NumPy pickle loading stays disabled.
    manifest["vectors_sha256"] = hashlib.sha256(vector_path.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        VectorIndex.load(tmp_path)


def test_metadata_tampering_and_missing_provenance_rejected(tmp_path):
    VectorIndex(SPEC, [record("a")], [[1, 0, 0]], "v1").save(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    (tmp_path / manifest["records_file"]).write_text('{}\n')
    with pytest.raises(ValueError, match="checksum"):
        VectorIndex.load(tmp_path)
    with pytest.raises(ValueError, match="source"):
        VectorIndex(SPEC, [record("a", source_url="")], [[1, 0, 0]], "v1")
    with pytest.raises(ValueError, match="owner"):
        VectorIndex(SPEC, [record("a", access_scope="private")], [[1, 0, 0]], "v1")


class ToyEncoder:
    """Only replaces external model inference; disk, image and retrieval stay real."""

    spec = SPEC

    def __init__(self):
        self.images_seen = 0
        self.texts_seen = 0

    def encode_images(self, paths):
        self.images_seen += len(paths)
        return np.array([[1, 0, 0] for path in paths], dtype=np.float32)

    def encode_texts(self, texts):
        self.texts_seen += len(texts)
        return np.array([[0, 1, 0] for text in texts], dtype=np.float32)


def asset(tmp_path, asset_id="real", **extra):
    path = tmp_path / f"{asset_id}.png"
    Image.new("RGB", (5, 8), (150, 10, 10)).save(path)
    return {
        "asset_id": asset_id, "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "status": "ready",
        "source_url": "https://example.org/source", "image_url": "https://example.org/image",
        "title": "2025-26 Topps basketball trading card", "caption": "A real card example",
        "side": "unknown", "image_type": "official_design", "season_start": 2025,
        **extra,
    }


def test_real_file_indexing_resume_and_caption_changes_invalidate_text_only(tmp_path):
    row = asset(tmp_path)
    encoder = ToyEncoder()
    index, report = build_asset_index([row], encoder, tmp_path / "cache", "v1", batch_size=1)
    assert report["image_vectors"] == 1 and report["text_vectors"] == 1
    assert {r["modality"] for r in index.records} == {"text", "image"}
    assert all(r["source_sha256"] == row["sha256"] for r in index.records)
    again, report = build_asset_index([row], encoder, tmp_path / "cache", "v1")
    assert report["cache_hits"] == 2
    assert encoder.images_seen == 1 and encoder.texts_seen == 1
    _, report = build_asset_index([{**row, "caption": "A changed caption"}], encoder,
                                  tmp_path / "cache", "v1")
    assert report["cache_hits"] == 1
    assert encoder.images_seen == 1 and encoder.texts_seen == 2
    assert search_text(again, encoder, "card", modality="image")[0]["asset_id"] == "real"
    assert search_image(again, encoder, row["path"], modality="image")[0]["score"] == pytest.approx(1)


def test_query_helpers_default_to_image_candidates_instead_of_mixing_modalities(tmp_path):
    row = asset(tmp_path)
    encoder = ToyEncoder()
    index, _ = build_asset_index([row], encoder, tmp_path / "cache", "v1")
    assert [hit["modality"] for hit in search_text(index, encoder, "card")] == ["image"]
    assert [hit["modality"] for hit in search_image(index, encoder, row["path"])] == ["image"]


def test_bad_assets_report_failures_without_inventing_vectors(tmp_path):
    good = asset(tmp_path)
    rows = [good, asset(tmp_path, "wrong-hash", sha256="b" * 64),
            {**good, "asset_id": "missing", "path": str(tmp_path / "missing.png")},
            {**good, "asset_id": "failed", "status": "download_failed"},
            {**good, "asset_id": "secret", "access_scope": "private", "owner_id": "alice"}]
    index, report = build_asset_index(rows, ToyEncoder(), tmp_path / "cache", "v1")
    assert len(index.records) == 2
    assert report["failed_assets"] == 3
    assert report["skipped_assets"] == 1
    assert {r["asset_id"] for r in index.records} == {"real"}
    assert {r["reason"] for r in report["failures"]} == {
        "source_hash_mismatch", "file_missing", "private_asset_not_authorized"}


def test_private_visibility_cannot_be_promoted_by_missing_access_scope(tmp_path):
    row = asset(tmp_path, visibility="private", owner_id="alice")
    index, report = build_asset_index([row], ToyEncoder(), tmp_path / "cache", "v1")
    assert index.records == []
    assert report["failed_assets"] == 1
    assert report["failures"][0]["reason"] == "private_asset_not_authorized"


def test_inference_failure_is_recorded_and_retry_can_recover(tmp_path):
    class BrokenEncoder(ToyEncoder):
        def encode_images(self, paths):
            raise RuntimeError("model unavailable")

    row = asset(tmp_path)
    index, report = build_asset_index([row], BrokenEncoder(), tmp_path / "cache", "v1")
    assert len(index.records) == 1
    assert index.records[0]["modality"] == "text"
    assert report["failed_embeddings"] == 1
    recovered, report = build_asset_index([row], ToyEncoder(), tmp_path / "cache", "v1")
    assert len(recovered.records) == 2
    assert report["cache_hits"] == 1


def test_cli_inspects_portable_index_without_loading_optional_model(tmp_path):
    VectorIndex(SPEC, [record("a")], [[1, 0, 0]], "v1").save(tmp_path)
    script = Path(__file__).resolve().parents[3] / "scripts/index_knowledge_vectors.py"
    result = subprocess.run([sys.executable, str(script), "--output", str(tmp_path), "--inspect"],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["count"] == 1
    assert summary["modalities"] == {"image": 1}
    assert summary["knowledge_version"] == "v1"


def test_model_provenance_cannot_relabel_unverified_weights(tmp_path):
    # A forged provenance file must fail before optional model code is loaded.
    (tmp_path / "provenance.json").write_text(json.dumps({
        "model_id": "google/siglip-base-patch16-224",
        "revision": "7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed",
        "files": {"model.safetensors": "a" * 64},
    }))
    with pytest.raises(ValueError, match="pinned weight"):
        SiglipEncoder.validated_provenance(tmp_path)


def test_preprocessing_identity_tracks_tokenizer_configuration_and_libraries():
    provenance = {"files": {"model.safetensors": "a" * 64, "tokenizer_config.json": "b" * 64}}
    first = SiglipEncoder.model_spec(provenance, 768, {"pillow": "1"})
    different_token_limit = {"files": {**provenance["files"], "tokenizer_config.json": "c" * 64}}
    assert SiglipEncoder.model_spec(different_token_limit, 768,
                                   {"pillow": "1"}).fingerprint != first.fingerprint
    assert SiglipEncoder.model_spec(provenance, 768,
                                   {"pillow": "2"}).fingerprint != first.fingerprint
