"""Rebuildable learned image/text vectors, kept outside the online API model path.

The portable index uses float32 NumPy arrays and JSON only. Scores produce
source-linked candidates, never identity, parallel, condition or price facts.
Optional model dependencies are imported only by ``SiglipEncoder``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol, Sequence
from urllib.parse import urlparse

import numpy as np
from PIL import Image, ImageOps


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(_json(value) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    revision: str
    dimension: int
    preprocessing: str
    embedding_space: str

    def __post_init__(self) -> None:
        if not all((self.model_id, self.revision, self.preprocessing, self.embedding_space)):
            raise ValueError("model identity and preprocessing must be explicit")
        if not isinstance(self.dimension, int) or self.dimension <= 0:
            raise ValueError("invalid model dimension")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_json(asdict(self)).encode()).hexdigest()


def _normalized(values: Any, dimension: int) -> np.ndarray:
    matrix = np.asarray(values, dtype=np.float32)
    if matrix.size == 0:
        matrix = np.empty((0, dimension), dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] != dimension:
        raise ValueError("vector dimension mismatch")
    if not np.isfinite(matrix).all():
        raise ValueError("non-finite vector")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if (norms <= 1e-12).any():
        raise ValueError("zero vector")
    return matrix / norms


def _validate_record(record: dict[str, Any]) -> None:
    if not record.get("vector_id") or not record.get("asset_id"):
        raise ValueError("missing vector/asset identity")
    if record.get("modality") not in {"text", "image"}:
        raise ValueError("invalid vector modality")
    if record.get("side") not in {"front", "back", "unknown", "detail", "both"}:
        raise ValueError("invalid image side")
    scope = record.get("access_scope")
    if scope not in {"public_reference", "private"}:
        raise ValueError("explicit access scope required")
    if scope == "private" and not record.get("owner_id"):
        raise ValueError("private vector requires owner")
    source_url = urlparse(str(record.get("source_url", "")))
    if source_url.scheme not in {"http", "https"} or not source_url.netloc:
        raise ValueError("valid source URL required")
    checksum = record.get("source_sha256", "")
    if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
        raise ValueError("valid source SHA256 required")


class VectorIndex:
    def __init__(self, model: ModelSpec, records: Sequence[dict[str, Any]], vectors: Any,
                 knowledge_version: str):
        if not knowledge_version:
            raise ValueError("knowledge version required")
        self.model = model
        self.knowledge_version = knowledge_version
        self.records = [dict(record) for record in records]
        identities: set[str] = set()
        for record in self.records:
            _validate_record(record)
            if record["vector_id"] in identities:
                raise ValueError("duplicate vector identity")
            identities.add(record["vector_id"])
        self.vectors = _normalized(vectors, model.dimension)
        if len(self.records) != len(self.vectors):
            raise ValueError("vector and record count mismatch")

    def save(self, output: str | Path) -> dict[str, Any]:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        generation = uuid.uuid4().hex
        vectors_file = f"vectors-{generation}.npy"
        records_file = f"records-{generation}.jsonl"
        with (output / vectors_file).open("wb") as stream:
            np.save(stream, self.vectors, allow_pickle=False)
        (output / records_file).write_text(
            "".join(_json(record) + "\n" for record in self.records), encoding="utf-8")
        manifest = {
            "format": "card-knowledge-exact-cosine-v1", "model": asdict(self.model),
            "knowledge_version": self.knowledge_version, "count": len(self.records),
            "vectors_file": vectors_file, "records_file": records_file,
            "vectors_sha256": file_sha256(output / vectors_file),
            "records_sha256": file_sha256(output / records_file),
            "normalized": True, "dtype": "float32",
        }
        # Readers see either complete generation; older generations permit rollback.
        _atomic_json(output / "manifest.json", manifest)
        return manifest

    @classmethod
    def load(cls, output: str | Path, expected_model: ModelSpec | None = None) -> VectorIndex:
        output = Path(output)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != "card-knowledge-exact-cosine-v1":
            raise ValueError("unsupported vector index format")
        model = ModelSpec(**manifest["model"])
        if expected_model is not None and model != expected_model:
            raise ValueError("model identity mismatch; rebuild a separate index")
        paths = []
        for name in ("vectors", "records"):
            filename = manifest[f"{name}_file"]
            if Path(filename).name != filename:
                raise ValueError("invalid index artifact path")
            path = output / filename
            if file_sha256(path) != manifest[f"{name}_sha256"]:
                raise ValueError(f"{name} checksum mismatch")
            paths.append(path)
        vectors = np.load(paths[0], allow_pickle=False, mmap_mode="r")
        if vectors.dtype != np.dtype("float32"):
            raise ValueError("index must contain float32 vectors")
        records = [json.loads(line) for line in paths[1].read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        if len(records) != manifest["count"]:
            raise ValueError("manifest count mismatch")
        return cls(model, records, vectors, manifest["knowledge_version"])

    def search(self, vector: Any, *, model: ModelSpec, top_k: int = 10,
               modality: str | None = None, side: str | None = None,
               owner_id: str | None = None,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if model != self.model:
            raise ValueError("query model differs from index model")
        if top_k < 0:
            raise ValueError("top_k must be nonnegative")
        query = _normalized([vector], model.dimension)[0]
        selected = []
        for position, record in enumerate(self.records):
            if record["access_scope"] == "private" and (
                not owner_id or record["owner_id"] != owner_id
            ):
                continue
            if modality is not None and record["modality"] != modality:
                continue
            if side is not None and record["side"] != side:
                continue
            if any(record.get(key) != value for key, value in (filters or {}).items()):
                continue
            selected.append(position)
        if not selected or top_k == 0:
            return []
        scores = self.vectors[selected] @ query
        ranking = sorted(zip(selected, scores, strict=True),
                         key=lambda item: (-float(item[1]), self.records[item[0]]["vector_id"]))
        return [{**self.records[position], "score": float(score),
                 "knowledge_version": self.knowledge_version,
                 "model_fingerprint": self.model.fingerprint,
                 "candidate_only": True} for position, score in ranking[:top_k]]


class JointEncoder(Protocol):
    spec: ModelSpec

    def encode_images(self, paths: Sequence[str | Path]) -> np.ndarray: ...

    def encode_texts(self, texts: Sequence[str]) -> np.ndarray: ...


def asset_text(asset: dict[str, Any]) -> str:
    """Bounded descriptive English input; exact card numbers remain in metadata."""
    parts = ["Basketball trading card reference.", str(asset.get("title") or ""),
             str(asset.get("caption") or ""), str(asset.get("publisher") or "")]
    if asset.get("season_start") is not None:
        parts.append(f"Season {asset['season_start']}-{asset.get('season_end', '')}.")
    if asset.get("side") in {"front", "back"}:
        parts.append(f"Card {asset['side']}.")
    return " ".join(part.strip() for part in parts if part.strip())


def build_asset_index(assets: Sequence[dict[str, Any]], encoder: JointEncoder,
                      cache_dir: str | Path, knowledge_version: str, *, batch_size: int = 8
                      ) -> tuple[VectorIndex, dict[str, Any]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    started = time.monotonic()
    cache = Path(cache_dir) / encoder.spec.fingerprint
    cache.mkdir(parents=True, exist_ok=True)
    vectors: list[np.ndarray] = []
    records: list[dict[str, Any]] = []
    pending: dict[str, list[tuple[dict[str, Any], str, Path]]] = {"image": [], "text": []}
    report: dict[str, Any] = {
        "assets_seen": len(assets), "image_vectors": 0, "text_vectors": 0,
        "cache_hits": 0, "skipped_assets": 0, "failed_assets": 0,
        "failed_embeddings": 0, "failures": [], "model": asdict(encoder.spec),
    }

    def fail(asset_id: str, reason: str, *, modality: str | None = None) -> None:
        report["failed_embeddings" if modality else "failed_assets"] += 1
        report["failures"].append({"asset_id": asset_id, "reason": reason,
                                   **({"modality": modality} if modality else {})})

    def append(record: dict[str, Any], vector: Any) -> None:
        vector = _normalized([vector], encoder.spec.dimension)[0]
        vectors.append(vector)
        records.append(record)
        report[f"{record['modality']}_vectors"] += 1

    def flush(modality: str) -> None:
        batch = pending[modality]
        if not batch:
            return
        try:
            values = (encoder.encode_images([item[1] for item in batch]) if modality == "image"
                      else encoder.encode_texts([item[1] for item in batch]))
            values = _normalized(values, encoder.spec.dimension)
            if len(values) != len(batch):
                raise ValueError("encoder result count mismatch")
            for (record, _, path), vector in zip(batch, values, strict=True):
                temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
                with temporary.open("wb") as stream:
                    np.save(stream, vector, allow_pickle=False)
                os.replace(temporary, path)
                append(record, vector)
        except Exception:
            # A bad image must not suppress valid neighbours; retry individually.
            if len(batch) > 1:
                for item in batch[:]:
                    pending[modality] = [item]
                    flush(modality)
            else:
                fail(batch[0][0]["asset_id"], "encoder_failed", modality=modality)
        pending[modality] = []

    seen: set[str] = set()
    for asset in assets:
        asset_id = str(asset.get("asset_id") or "")
        if asset.get("status") not in {"ready", "downloaded", "available", "ok", "success"}:
            report["skipped_assets"] += 1
            continue
        if asset_id in seen:
            fail(asset_id, "duplicate_asset_id")
            continue
        seen.add(asset_id)
        if (asset.get("access_scope", "public_reference") != "public_reference"
                or asset.get("visibility", "public_reference") != "public_reference"):
            fail(asset_id, "private_asset_not_authorized")
            continue
        path = Path(asset.get("path") or "")
        if not path.is_file():
            fail(asset_id, "file_missing")
            continue
        if file_sha256(path) != asset.get("sha256"):
            fail(asset_id, "source_hash_mismatch")
            continue
        try:
            with Image.open(path) as image:
                image.verify()
            base = {**asset, "source_sha256": asset["sha256"],
                    "access_scope": "public_reference", "side": asset.get("side") or "unknown"}
            _validate_record({**base, "vector_id": f"{asset_id}:image", "modality": "image"})
        except Exception:
            fail(asset_id, "invalid_image_or_provenance")
            continue
        for modality, value in (("image", str(path)), ("text", asset_text(asset))):
            content_hash = (asset["sha256"] if modality == "image"
                            else hashlib.sha256(value.encode()).hexdigest())
            cache_path = cache / f"{modality}-{content_hash}.npy"
            record = {**base, "vector_id": f"{asset_id}:{modality}", "modality": modality,
                      "content_sha256": content_hash,
                      **({"text": value} if modality == "text" else {})}
            if cache_path.is_file():
                try:
                    append(record, np.load(cache_path, allow_pickle=False))
                    report["cache_hits"] += 1
                    continue
                except (ValueError, OSError):
                    pass  # Re-encode corrupt/invalid cache entries.
            pending[modality].append((record, value, cache_path))
            if len(pending[modality]) >= batch_size:
                flush(modality)
    flush("image")
    flush("text")
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["status"] = ("partial" if report["failed_assets"] or report["failed_embeddings"]
                        else "ready" if records else "empty")
    return VectorIndex(encoder.spec, records, vectors, knowledge_version), report


def search_text(index: VectorIndex, encoder: JointEncoder, text: str,
                **options: Any) -> list[dict[str, Any]]:
    if not text.strip():
        raise ValueError("query text required")
    if encoder.spec != index.model:
        raise ValueError("query model differs from index model")
    options.setdefault("modality", "image")
    return index.search(encoder.encode_texts([text])[0], model=encoder.spec, **options)


def search_image(index: VectorIndex, encoder: JointEncoder, path: str | Path,
                 **options: Any) -> list[dict[str, Any]]:
    if encoder.spec != index.model:
        raise ValueError("query model differs from index model")
    options.setdefault("modality", "image")
    return index.search(encoder.encode_images([path])[0], model=encoder.spec, **options)


class SiglipEncoder:
    """Pinned local Google SigLIP joint encoder; never fetches code or online images."""

    MODEL_ID = "google/siglip-base-patch16-224"
    REVISION = "7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed"
    WEIGHTS_SHA256 = "2c63cb7d1f2e95ba501893cbb8faeb4ea9a3af295498d35097126228659c2af8"

    @classmethod
    def validated_provenance(cls, model_dir: str | Path) -> dict[str, Any]:
        model_dir = Path(model_dir)
        provenance = json.loads((model_dir / "provenance.json").read_text())
        if provenance.get("model_id") != cls.MODEL_ID or provenance.get("revision") != cls.REVISION:
            raise ValueError("model provenance differs from pinned model")
        files = provenance.get("files", {})
        if files.get("model.safetensors") != cls.WEIGHTS_SHA256:
            raise ValueError("model provenance differs from pinned weights")
        required = {"config.json", "preprocessor_config.json", "model.safetensors",
                    "tokenizer_config.json", "tokenizer.json", "special_tokens_map.json"}
        if not required.issubset(files):
            raise ValueError("incomplete model provenance")
        for filename, checksum in files.items():
            if Path(filename).name != filename or file_sha256(model_dir / filename) != checksum:
                raise ValueError("model artifact checksum mismatch")
        return provenance

    @classmethod
    def model_spec(cls, provenance: dict[str, Any], dimension: int,
                   libraries: dict[str, str]) -> ModelSpec:
        preprocessing = {
            "adapter": "siglip-exif-rgb-full-resize-v1", "libraries": libraries,
            "dtype": "float32", "normalize": "l2", "text_padding": "max_length",
            "text_truncation": True,
            "model_artifact_sha256": {name: checksum for name, checksum in
                                      provenance["files"].items() if name != "README.md"},
        }
        return ModelSpec(cls.MODEL_ID, cls.REVISION, dimension,
                         _json(preprocessing), "siglip-joint-image-text")

    def __init__(self, model_dir: str | Path, *, device: str = "cpu"):
        provenance = self.validated_provenance(model_dir)
        import torch
        from transformers import AutoModel, AutoProcessor

        model_dir = Path(model_dir)
        self.torch = torch
        self.device = device
        self.processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True,
                                                      trust_remote_code=False, use_fast=True)
        self.model = AutoModel.from_pretrained(model_dir, local_files_only=True,
                                              trust_remote_code=False, use_safetensors=True,
                                              attn_implementation="eager").eval().to(device)
        libraries = {name: version(name) for name in
                     ("torch", "torchvision", "transformers", "pillow", "tokenizers",
                      "sentencepiece", "protobuf")}
        self.spec = self.model_spec(provenance, int(self.model.config.text_config.hidden_size),
                                    libraries)

    def encode_images(self, paths: Sequence[str | Path]) -> np.ndarray:
        images = []
        for path in paths:
            with Image.open(path) as original:
                images.append(ImageOps.exif_transpose(original).convert("RGB"))
        try:
            values = self.processor(images=images, return_tensors="pt").to(self.device)
            with self.torch.inference_mode():
                features = self.model.get_image_features(**values)
            return _normalized(features.float().cpu().numpy(), self.spec.dimension)
        finally:
            for image in images:
                image.close()

    def encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        values = self.processor(text=list(texts), padding="max_length", truncation=True,
                                return_tensors="pt").to(self.device)
        with self.torch.inference_mode():
            features = self.model.get_text_features(**values)
        return _normalized(features.float().cpu().numpy(), self.spec.dimension)
