#!/usr/bin/env python3
"""Stage reviewed specialist evidence into a separate annual-content batch.

No network or database writes. Original published files are never edited.
Official facts remain separate from attributed third-party observations.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil

from PIL import Image


def identity_is_publishable(identity):
    return bool(identity and identity.get("status") in {
        "unmatched_official_card", "core_identity_matched_variant_unresolved",
        "exact_official_section_matched",
    })


def validate_scan_binding(record, identity):
    source = identity.get("source_identity", {})
    if (record["source_key"] != identity.get("source_key")
            or record["page_url"] != source.get("source_page_url")
            or record["source_artifact_sha256"] != source.get("source_artifact_sha256")):
        raise ValueError("scan identity review source mismatch")
    for image in record["image_urls_observed"]:
        if not any(e.get("side") == image.get("side") and e.get("sha256") == image.get("sha256")
                   for e in identity.get("image_evidence", [])):
            raise ValueError("scan image is absent from identity review")


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def stage(base: Path, root: Path, research: Path) -> dict:
    base, root, research = base.resolve(), root.resolve(), research.resolve()
    if root == base or root.parent != base.parent:
        raise ValueError("supplement must use an independent sibling batch")
    now = datetime.now(timezone.utc).isoformat()
    inventory = read(base / "inventory.json")
    products = {p["source_key"]: p for p in inventory["products"]}
    manifests = {key: read(base / "text/manifests" / f"{key}.json") for key in products}
    # Start from the unchanged baseline, so repeated staging never duplicates assets.
    assets = [json.loads(line.replace(str(base), str(root)))
              for line in (base / "media/assets.jsonl").read_text().splitlines() if line]
    baseline_count = len(assets)

    def capture(path, checksum=None):
        path = Path(path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        data = path.read_bytes()
        digest = sha256(data).hexdigest()
        if checksum and digest != checksum:
            raise ValueError(f"capture checksum mismatch: {path}")
        target = root / "media/sources" / (digest + path.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != data:
            raise ValueError("immutable source collision")
        if not target.exists():
            shutil.copy2(path, target)
        return target, digest

    def public_path(path):
        return root.name + "/" + path.relative_to(root).as_posix()

    def add_image(record, image, *, image_type, caption, source_path, source_hash, review_path,
                  identity=None, verification=None):
        product = products[record["source_key"]]
        data = Path(image["path"]).read_bytes()
        digest = sha256(data).hexdigest()
        if digest != image["sha256"]:
            raise ValueError("image checksum mismatch")
        with Image.open(image["path"]) as decoded:
            decoded.load()
            width, height = decoded.size
            fmt = decoded.format
        if (width, height) != (image["width"], image["height"]):
            raise ValueError("image dimension mismatch")
        target = root / "media/originals" / (digest + Path(image["path"]).suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
        asset = {
            "asset_id": "knowledge-media-" + sha256((record["source_key"] + digest).encode()).hexdigest()[:24],
            "path": str(target), "sha256": digest, "source_url": record["page_url"],
            "image_url": image["url"], "source_key": record["source_key"], "title": record["title"],
            "publisher": product["publisher"], "season_start": product["season_start"],
            "season_end": product["season_end"], "side": image.get("side", "unknown"),
            "image_type": image_type, "caption": caption, "status": "ready",
            "captured_at": image.get("captured_at") or record.get("page_capture", {}).get("captured_at"),
            "reviewed_at": now, "width": width, "height": height, "format": fmt,
            "source_artifact_path": str(source_path), "source_artifact_sha256": source_hash,
            "source_authority": record["source_authority"], "locator": image.get("locator") or image["url"],
            "review_artifact_path": str(review_path),
            "review_artifact_sha256": sha256(review_path.read_bytes()).hexdigest(),
            "review_basis": "Actual image and source review; official identity gate recorded separately.",
            "product_binding_evidence": {"url": record["page_url"], "capture_kind": record.get("capture_kind", "original_html")},
            "card_number": None, "player": None, "variant": None, "pair_id": None,
            "visibility": "public_reference", "usage_scope": "internal_reference_research",
            "rights": "Original source rights retained; public access is not a redistribution license.",
            "rendition": image.get("rendition"), "original_resolution": image.get("original_resolution"),
        }
        if identity:
            asset.update(source_card_identity=record["identity_fields"], identity_conflicts=identity.get("conflicts", []))
        if verification:
            cert = record["identity_fields"]["certification_number"]
            asset.update(player=identity["canonical_player"], card_number=identity["card_number"],
                         variant=identity.get("variant"), identity_verification=verification,
                         pair_id="psa:" + cert, pair_evidence={"record_type": "certification",
                         "record_id": cert, "record_url": record["page_url"]})
        assets.append(asset)
        return asset

    # Publish only captured, explicitly attributed product claims.
    checklist = read(research / "checklists.json")
    capture(research / "checklists.json")
    text_count = 0
    for record in checklist["products"]:
        evidence = record["evidence"]
        source, digest = capture(evidence["capture_path"], evidence["capture_sha256"])
        attrs = manifests[record["source_key"]]["entities"][0]["attributes"]
        attrs.setdefault("supplementary_evidence", []).append({
            "source_url": record["page_url"], "site": record["site"],
            "authority": record["source_authority"], "capture_kind": evidence["capture_kind"],
            "source_artifact": public_path(source), "source_sha256": digest,
            "observed_at": checklist["captured_at"], "facts": record["facts"],
            "limits": record["limits"],
        })
        text_count += len(record["facts"])

    checks = read(research / "identity-checks.json")
    if checks.get("input_sha256") != sha256((research / "scans.json").read_bytes()).hexdigest():
        raise ValueError("identity review does not match frozen scan research")
    identity_report, _ = capture(research / "identity-checks.json")
    by_cert = {r["certification_number"]: r for r in checks["records"]}
    scans = read(research / "scans.json")
    capture(research / "scans.json")
    if scans.get("search_disposition_capture_path"):
        capture(scans["search_disposition_capture_path"], scans.get("search_disposition_capture_sha256"))
    for path in scans.get("qa_contact_sheets", []):
        capture(path)
    held = []
    for record in scans["records"]:
        cert = record["identity_fields"]["certification_number"]
        identity = by_cert.get(cert)
        if not identity_is_publishable(identity):
            held.append({"cert": cert, "reason": "official product/season identity review pending or ambiguous"})
            continue
        validate_scan_binding(record, identity)
        source, digest = capture(record["source_artifact_path"], record["source_artifact_sha256"])
        primary_refs = []
        for primary in identity.get("primary_binding_evidence", []):
            if primary.get("capture_path"):
                primary_path, primary_sha = capture(primary["capture_path"], primary.get("sha256") or primary.get("capture_sha256"))
                primary_refs.append({"url": primary["url"], "source_artifact": public_path(primary_path),
                    "sha256": primary_sha, "binding_basis": primary.get("binding_basis")})
        attrs = manifests[record["source_key"]]["entities"][0]["attributes"]
        attrs.setdefault("supplementary_card_records", []).append({
            "authority": record["source_authority"], "source_url": record["page_url"],
            "source_artifact": public_path(source), "source_sha256": digest,
            "capture_kind": record["capture_kind"], "source_fields": record["identity_fields"],
            "official_match_status": identity["status"], "candidate_card_ids": identity["candidate_card_ids"],
            "conflicts": identity["conflicts"], "reason": identity["reason"],
            "pair_evidence": record["pair_evidence"], "limits": record["limits"],
            "primary_binding_evidence": primary_refs,
        })
        verification = None
        if identity["candidate_card_ids"]:
            artifact = root / "text/artifacts" / Path(identity["artifact_path"]).name
            verification = {"status": "verified", "card_id": identity["chosen_card_id"],
                "candidate_card_ids": identity["candidate_card_ids"], "artifact_path": str(artifact),
                "artifact_sha256": identity["artifact_checksum"], "source_url": record["page_url"],
                "source_sha256": digest, "source_locator": "PSA cert " + cert,
                "scope": identity["binding_scope"], "conflicts": identity["conflicts"],
                "primary_binding_evidence": primary_refs}
        for image in record["image_urls_observed"]:
            if not (image.get("visual_inspected") and image.get("visible_cert_matches_record")):
                raise ValueError("unreviewed certification image")
            add_image(record, image, image_type="verified_card_scan" if verification else "third_party_card_scan",
                caption=f"{record['title']}; {identity['canonical_player']} #{identity['card_number']}; "
                        f"{image['side']} of PSA cert {cert}. Parallel only as explicitly verified; small slab rendition.",
                source_path=source, source_hash=digest, review_path=identity_report,
                identity=identity, verification=verification)

    # Two full manufacturer design sheets, not six separately acquired specimens.
    databases = read(research / "databases.json")
    for record in databases["records"]:
        if record["site"] != "Trading Card Journal":
            continue
        source, digest = capture(record["page_capture"]["path"], record["page_capture"]["sha256"])
        review, _ = capture(research / "databases.json")
        for image in record["image_captures"]:
            if not any(f"Hobby-{n}-" in image["url"] for n in (2, 3)):
                continue
            add_image(record, image, image_type="manufacturer_design_sheet",
                caption="2024 Panini Select WNBA Hobby manufacturer promotional sheet. Three design examples, not printed specimen photos; no card identity or pair asserted.",
                source_path=source, source_hash=digest, review_path=review)

    for key, manifest in manifests.items():
        text = json.dumps(manifest, ensure_ascii=False, indent=2).replace(str(base), str(root))
        (root / "text/manifests" / f"{key}.json").write_text(text + "\n")
    (root / "media/assets.jsonl").write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in assets))
    counts = Counter(a["source_key"] for a in assets)
    report = read(base / "media/report.json")
    dispositions = {p["source_key"]: p for p in scans.get("original_83_missing_product_dispositions", [])}
    for product in report["products"]:
        key = product["source_key"]
        if key in dispositions:
            product["specialist_search"] = dispositions[key]
        added = [a for a in assets[baseline_count:] if a["source_key"] == key]
        product["asset_count"] = counts[key]
        if added:
            product.update(status="reviewed_partial_references", coverage_status="partial",
                identity_bound_cards=len({a["pair_id"] for a in added if a.get("pair_id")}),
                verified_pairs=len({a["pair_id"] for a in added if a.get("pair_id")}),
                failure_reasons=[], limits="Representative reviewed source samples only; complete-set coverage unknown.")
            product["sources"].extend({"url": a["source_url"], "authority": a["source_authority"]} for a in added)
    pairs = len({a["pair_id"] for a in assets if a.get("pair_id")})
    report.update(captured_at=now, asset_count=len(assets), products_with_images=len(counts),
        verified_specimen_count=pairs, verified_pair_count=pairs,
        identity_bound_card_count=pairs,
        source_authority_counts=dict(Counter(a["source_authority"] for a in assets)),
        side_counts=dict(Counter(a["side"] for a in assets)),
        source_status_counts=dict(Counter(p["status"] for p in report["products"])),
        limits=["Representative samples only; whole-set coverage remains unknown.",
                "Third-party product/card claims remain separate from official checklist facts.",
                "Verified pair count requires official card-family binding plus matching physical certification.",
                "PSA captures use the public small slab rendition, not original high-resolution scans."])
    report["seasons"] = [{"season_start": year,
        "known_product_count": sum(p["season_start"] == year for p in products.values()),
        "products_with_images": len({a["source_key"] for a in assets if a["season_start"] == year}),
        "reference_images": sum(a["season_start"] == year for a in assets),
        "verified_pairs": len({a["pair_id"] for a in assets if a["season_start"] == year and a.get("pair_id")}),
        "annual_discovery_complete": False, "full_card_front_back_coverage": None}
        for year in sorted({p["season_start"] for p in products.values()})]
    summary = {"staged_at": now, "baseline_assets": baseline_count, "new_images": len(assets)-baseline_count,
        "total_images": len(assets), "products_with_images": len(counts), "verified_pairs": pairs,
        "supplementary_product_fact_count": text_count, "held_certificates": held,
        "official_card_fact_count_unchanged": 701473}
    write(root / "media/report.json", report)
    write(root / "media/supplement-report.json", summary)
    files = {p.relative_to(root / "media").as_posix(): sha256(p.read_bytes()).hexdigest()
             for p in (root / "media").rglob("*") if p.is_file() and p.name != "snapshot.json"}
    write(root / "media/snapshot.json", {"verified_at": now, "asset_count": len(assets),
        "products_with_images": len(counts), "verified_pairs": pairs, "files": files, "errors": [],
        "scope_complete": False, "supplement": summary})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--research", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.base, args.root, args.research), ensure_ascii=False, indent=2))
