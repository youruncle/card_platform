#!/usr/bin/env python3
"""Prepare a checksum-verified three-season content release without database writes."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))

from card_platform.knowledge_manifest import validate_manifest  # noqa: E402
from card_platform.knowledge_release import attach_reference_images  # noqa: E402
from card_platform.knowledge_vectors import VectorIndex  # noqa: E402


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def validate_vector_generation(assets, index, build_report, assets_sha256):
    public_assets = {a["asset_id"]: a for a in assets if a.get("status") == "ready" and a.get("visibility") == "public_reference"}
    asset_ids = set(public_assets)
    indexed = {r["asset_id"] for r in index.records if r["modality"] == "image"}
    if indexed != asset_ids:
        raise ValueError("Media and vector generation are not synchronized")
    if (build_report["assets_sha256"] != assets_sha256
            or build_report["knowledge_version"] != index.knowledge_version
            or build_report["model_fingerprint"] != index.model.fingerprint):
        raise ValueError("Vector build does not match current media, model or knowledge batch")
    if len(index.records) != 2 * len(asset_ids):
        raise ValueError("Every released asset requires one image and one text vector")
    for asset_id in asset_ids:
        if Counter(r["modality"] for r in index.records if r["asset_id"] == asset_id) != {"image": 1, "text": 1}:
            raise ValueError("Every released asset requires one image and one text vector")
    for record in index.records:
        asset = public_assets[record["asset_id"]]
        if record.get("sha256") != asset["sha256"] or record.get("source_key") != asset["source_key"]:
            raise ValueError("Vector source metadata differs from current reviewed asset")
    return asset_ids


def prepare(root: Path) -> dict:
    inventory = json.loads((root / "inventory.json").read_text())
    compile_report = json.loads((root / "text/compile-report.json").read_text())
    assets = [json.loads(line) for line in (root / "media/assets.jsonl").read_text().splitlines() if line]
    media_report = json.loads((root / "media/report.json").read_text())
    media_snapshot = json.loads((root / "media/snapshot.json").read_text())
    if media_snapshot.get("errors"):
        raise ValueError("Media snapshot has unresolved verification errors")
    for name, checksum in media_snapshot["files"].items():
        path = (root / "media" / name).resolve()
        if not path.is_relative_to((root / "media").resolve()) or sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError("Media audit snapshot checksum mismatch")
    media_products = {p["source_key"]: p for p in media_report["products"]}
    vector_manifest = json.loads((root / "vectors/manifest.json").read_text())
    build_report = json.loads((root / "vectors/build-report.json").read_text())
    index = VectorIndex.load(root / "vectors")
    now = datetime.now(timezone.utc)
    asset_ids = validate_vector_generation(assets, index, build_report,
                                           sha256((root / "media/assets.jsonl").read_bytes()).hexdigest())
    indexed = asset_ids
    by_key = {p["source_key"]: p for p in inventory["products"]}
    files, manifests, products = {}, [], []

    def add_file(path):
        path = Path(path).resolve()
        relative = path.relative_to(root.resolve()).as_posix()
        files[relative] = {"sha256": sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
        return relative

    for asset in assets:
        if asset["asset_id"] not in asset_ids:
            continue
        for field in ("path", "source_artifact_path", "review_artifact_path"):
            if asset.get(field):
                add_file(asset[field])
    for ref in compile_report["official_artifacts"]:
        relative = add_file(root / "text/artifacts" / ref["path"])
        if files[relative]["sha256"] != ref["checksum"]:
            raise ValueError("Text artifact checksum mismatch")
    for key, product in by_key.items():
        manifest = json.loads((root / "text/manifests" / f"{key}.json").read_text())
        manifest = attach_reference_images(manifest, assets, root / "media", now=now,
                                           asset_prefix=root.name + "/media")
        attrs = manifest["entities"][0]["attributes"]
        attrs["knowledge_content_release"] = {
            "batch_id": index.knowledge_version, "vector_manifest_sha256": sha256((root / "vectors/manifest.json").read_bytes()).hexdigest(),
            "model_fingerprint": index.model.fingerprint,
            "indexed_reference_images": sum(a["source_key"] == key and a["asset_id"] in indexed for a in assets),
            "query_access": "offline source-linked candidates; application query integration pending",
        }
        receipt = manifest["sources"][0].get("metadata", {}).get("capture_receipt", {})
        if receipt.get("path"):
            receipt["path"] = root.name + "/" + Path(receipt["path"]).resolve().relative_to(root.resolve()).as_posix()
        report = validate_manifest(manifest)
        if not report.publishable:
            raise ValueError(f"Invalid release manifest {key}: {report.conflicts}")
        relative = f"release/manifests/{key}.json"
        write_json(root / relative, manifest)
        add_file(root / relative)
        manifests.append(relative)
        images = attrs["reference_images"]
        product.update(text_status=attrs["text_evidence"]["status"],
                       source_freshness="verified_capture", refetched_at=manifest["sources"][0]["retrieved_at"],
                       source_checksum=manifest["sources"][0]["checksum"],
                       card_fact_count=attrs["text_evidence"]["card_fact_count"],
                       quarantined_rows=attrs["text_evidence"]["quarantined_rows"],
                       reference_images=len(images), paired_cards=attrs["knowledge_media_summary"]["paired_cards"],
                       image_status="partial_reference_samples" if images else "missing_reference_images",
                       vector_records=2 * len(images), vector_status="indexed_samples" if images else "pending_image_source",
                       full_image_percent=None)
        product.pop("text_error", None)
        product["media_acquisition"] = media_products.get(key, {"status": "no_source_discovered"})
        products.append(product)
    for sub in ("sources", "text/raw", "text/receipts", "media"):
        for path in (root / sub).rglob("*"):
            if path.is_file():
                add_file(path)
    for name in ("manifest.json", "build-report.json", "model-provenance.json", "query-smoke.json",
                 vector_manifest["records_file"], vector_manifest["vectors_file"]):
        if (root / "vectors" / name).exists():
            add_file(root / "vectors" / name)
    for path in (root / "vectors").glob("*.json"):
        add_file(path)
    for relative in ("inventory.json", "catalog.json", "text/compile-report.json", "media/assets.jsonl"):
        add_file(root / relative)
    summary = []
    for season in sorted({p["season_start"] for p in products}, reverse=True):
        selected = [p for p in products if p["season_start"] == season]
        summary.append({"season_start": season, "products": len(selected),
                        "publishers": dict(Counter(p["publisher"] for p in selected)),
                        "text_status": dict(Counter(p["text_status"] for p in selected)),
                        "card_facts": sum(p["card_fact_count"] for p in selected),
                        "reference_images": sum(p["reference_images"] for p in selected),
                        "products_with_images": sum(bool(p["reference_images"]) for p in selected),
                        "paired_cards": sum(p["paired_cards"] for p in selected),
                        "vector_records": sum(p["vector_records"] for p in selected)})
    bundle = {"batch_id": index.knowledge_version, "prepared_at": now.isoformat(),
              "summary": summary, "products": products, "manifests": manifests,
              "official_artifacts": compile_report["official_artifacts"], "files": files,
              "image_assets": len(asset_ids), "unique_image_bytes": len({a["sha256"] for a in assets if a["asset_id"] in asset_ids}),
              "vector_records": len(index.records), "paired_cards": sum(p["paired_cards"] for p in products),
              "completeness": "Known-source inventory; full annual product and card-image coverage unknown"}
    write_json(root / "release/inventory.json", {"batch_id": index.knowledge_version,
               "generated_at": now.isoformat(), "discovery_complete": False,
               "summary": summary, "products": products})
    add_file(root / "release/inventory.json")
    write_json(root / "release/bundle.json", bundle)
    return {k: bundle[k] for k in ("batch_id", "summary", "image_assets", "unique_image_bytes", "vector_records", "paired_cards")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "data/knowledge-three-seasons")
    print(json.dumps(prepare(parser.parse_args().root), ensure_ascii=False, indent=2))
