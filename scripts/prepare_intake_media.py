#!/usr/bin/env python3
"""Prepare reviewed intake media without fetching, encoding, publishing or DB writes.

Inputs are immutable research reports and the frozen cloud product inventory.
Only media/new-* outputs and audit/media-decisions.json are authored. The main
media/assets.jsonl belongs to the release integrator and is never overwritten.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "apps/api/src"))
from card_platform.knowledge_release import attach_reference_images  # noqa: E402

CONTRACT = "knowledge-intake-media-preparation-v1"
OLD_BEST = "topps-0fefa69ec5459817"
NEW_BEST = "topps-d4a74df398701268"
SINGLE_NBL = "topps-81ef25ea562f8f11"
CHROME_RETAIL = "topps-29864e06aab9d313"
CHROME_HOBBY = "topps-a97ac979ab102278"
CHROME_HOBBY_GREEN_SHA = "40201b5fbd7dbeb7aa9a3b95b9952652287e3c1d158445a4dbdf3de093dbdb7b"
CHROME_PARALLEL_SOURCE_SHA = "a8cb813ca56a4dc24f2fcf208018ac99171fc0a9dc8fa724ec5ca3c0d2cce1bd"


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(data)
    temporary.replace(path)


def resolve(value: str | None, batch: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    for candidate in (ROOT / path, batch / path):
        if candidate.is_file():
            return candidate.resolve()
    return (ROOT / path).resolve()


class EvidenceStore:
    def __init__(self, batch: Path, media: Path):
        self.batch, self.media = batch, media
        self.files: dict[str, dict] = {}

    def put(self, data: bytes, *, kind: str, suffix: str, original: Path | None = None) -> dict:
        checksum = digest(data)
        path = self.media / "sources" / kind / (checksum + suffix)
        self._write(path, data)
        return self._record(path, checksum, kind, original)

    def _write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != data:
            raise ValueError(f"content-addressed file differs: {path}")
        if not path.exists():
            path.write_bytes(data)

    def _record(self, path: Path, checksum: str, kind: str, original: Path | None) -> dict:
        record = {"path": path.relative_to(self.batch).as_posix(), "sha256": checksum,
                  "capture_kind": kind, "local_path": str(path.resolve())}
        if original:
            record["original_path"] = str(original.resolve())
        self.files[str(path)] = record
        return record

    def capture(self, value: str | None, capture_kind: str = "raw",
                expected: str | None = None) -> dict | None:
        source = resolve(value, self.batch)
        if source is None or not source.is_file():
            return None
        data = source.read_bytes()
        if expected and digest(data) != expected:
            raise ValueError(f"evidence checksum differs: {source}")
        lower = capture_kind.lower()
        if data.startswith(b"%PDF-"):
            group, suffix = "pdf", ".pdf"
        elif any(token in lower for token in ("reader", "extraction", "search")):
            group, suffix = "reader", ".json" if source.suffix == ".json" else ".txt"
        elif lower in {"review", "visual_review_contact_sheet"}:
            group, suffix = "review", source.suffix or ".bin"
        else:
            group, suffix = "raw", source.suffix if source.suffix in {".html", ".json", ".txt", ".jpg", ".png", ".gz"} else ".bin"
        record = self.put(data, kind=group, suffix=suffix, original=source)
        record["capture_kind"] = capture_kind
        record["storage_group"] = group
        return record

    def image(self, path: Path, data: bytes, info: dict | None) -> dict:
        suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "JPEG2000": ".jp2",
                  "TIFF": ".tiff", "GIF": ".gif"}.get((info or {}).get("format"), ".bin")
        checksum = digest(data)
        target = self.media / "originals" / (checksum + suffix)
        self._write(target, data)
        return self._record(target, checksum, "captured_image_bytes", path)


def decoded(data: bytes) -> dict:
    with Image.open(BytesIO(data)) as image:
        image.load()
        return {"width": image.width, "height": image.height, "format": image.format,
                "mode": image.mode}


def outcome(image: dict) -> tuple[str, str]:
    decision = image.get("decision") or image.get("review_status") or image.get("disposition")
    if decision not in {"accepted", "rejected"}:
        raise ValueError(f"candidate lacks a completed source review: {decision!r}")
    reason = image.get("reason") or image.get("review_reason") or image.get("disposition_reason")
    if not reason:
        raise ValueError("candidate review has no reason")
    return decision, reason


def contact_paths(image: dict) -> list[str]:
    paths = [image.get("contact_sheet_path")]
    if isinstance(image.get("review_basis"), dict):
        paths.append(image["review_basis"].get("contact_sheet_path"))
    if isinstance(image.get("locator"), dict):
        paths.append(image["locator"].get("qa_contact"))
    return list(dict.fromkeys(path for path in paths if path))


def canonical_key(product: dict, source: dict, image: dict | None = None) -> tuple[str, str | None]:
    key = product["source_key"]
    # This captured parallel is Hobby-specific; the same article's shared Base
    # card remains valid for Retail. Match both hashes, never the article alone.
    if (key == CHROME_RETAIL
            and source.get("sha256") == CHROME_PARALLEL_SOURCE_SHA
            and (image or {}).get("sha256") == CHROME_HOBBY_GREEN_SHA):
        return CHROME_HOBBY, "Captured CI parallel listing identifies Green Refractor /99 at 1:33 Hobby; Retail uses the distinct Topps Exclusive Green Refractor name. This exact Wembanyama Green image is rebound to Hobby; the article's shared Westbrook Base110 remains Retail."
    if "/cert/134204267/" in source.get("url", "") and key != "panini-api-2024-1415":
        return "panini-api-2024-1415", "Independent identity audit binds cert134204267 to the frozen2024-25 Crown Royale product; its earlier2023-24 assignment is rejected. Card-level identity remains unassigned in this preparer."
    if key != OLD_BEST:
        return key, None
    url = source.get("url", "")
    # A mixed article can still yield independently bound image cohorts.
    image_url = (image or {}).get("image_url", "")
    if "2024-25-Bowman-University-Best" in image_url:
        url = "2024-25-bowman-university-best"
    elif "2023-24-Bowman-University-Best" in image_url:
        url = "2023-24-bowman-university-best"
    if "2024-25-bowman-university-best" in url or "/cert/120524051/" in url:
        return NEW_BEST, "2024-25 Bowman U Best belongs to the existing 2024-25 source, including cert120524051; the calendar2024 source is a different product."
    if "2023-24-bowman-university-best" in url:
        return OLD_BEST, "2023-24 article cohort matches calendar2024 source: original checklist base16 Nicholas Boyd and Best of2023 program. Frozen canonical seasons are unchanged."
    return key, "UNPROVEN_BOWMAN_BEST_SUBSET"


def source_fact_decision(product: dict, source: dict, capture: dict | None) -> dict | None:
    """Exceptions to the assembler's default product-bound fact admission.

    A source hash can be shared by batch reader extracts, so the URL and original
    product key are part of the lookup as well. Never rebind all matching hashes
    from other product/certificate sections.
    """
    images = source.get("images", [])
    key = product["source_key"]
    target, correction = canonical_key(product, source)
    exception = key == OLD_BEST or target != key or any(
        im.get("review_id") in {"R13", "R14"} for im in images)
    if not exception or not source.get("extracted_facts"):
        return None
    cohorts = {year for im in images for year in ("2023-24", "2024-25")
               if f"{year}-Bowman-University-Best" in im.get("image_url", "")}
    row = {"collected_source_key": key, "source_url": source["url"],
           "source_capture_sha256": source.get("sha256"), "source_capture": capture,
           "extracted_fact_count": len(source["extracted_facts"]),
           "canonical_source_key": key}
    if len(cohorts) > 1:
        row.update(sourcefacts_disposition="not_admitted_product_scope_ambiguous",
                   reason="Source includes both Bowman U Best cohorts, so its facts cannot be uniquely assigned; independently bound image cohorts may still be admitted.")
    elif any(im.get("review_id") in {"R13", "R14"} for im in images):
        row.update(sourcefacts_disposition="not_admitted_product_scope_ambiguous",
                   reason="Hobby launch source is not established for the frozen Retail subset. R13 autograph subset is unproven and R14 Season's Best is absent from the Retail checklist; do not append this source's facts to Retail.")
    else:
        target, correction = canonical_key(product, source)
        row["canonical_source_key"] = target
        row.update(sourcefacts_disposition="rebind_to_existing_source" if target != key else
                   "not_admitted_product_scope_ambiguous" if correction == "UNPROVEN_BOWMAN_BEST_SUBSET" else
                   "admit_under_frozen_source", reason=correction)
    return row


def prepare(batch: Path, baseline_path: Path, result_paths: list[Path]) -> dict:
    media, audit = batch / "media", batch / "audit/media-decisions.json"
    media.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    baseline_bytes = baseline_path.read_bytes()
    baseline = json.loads(baseline_bytes)
    products = {p["attributes"]["source_key"]: p for p in baseline["products"]}
    assert len(products) == 126, "unexpected frozen product inventory"
    existing = {(key, ref["sha256"]): ref["asset_id"]
                for key, p in products.items() for ref in p["attributes"].get("reference_images", [])}
    store = EvidenceStore(batch, media)
    baseline_evidence = store.put(baseline_bytes, kind="review", suffix=".json", original=baseline_path)
    results = []
    for path in result_paths:
        data = path.read_bytes()
        results.append((path, json.loads(data), store.put(data, kind="review", suffix=".json", original=path)))
    # When a mistaken earlier cohort repeats a correctly scoped later record,
    # the native record is the sole accepted representative, regardless of order.
    native = {(p["source_key"], im.get("sha256"))
              for _, report, _ in results for p in report["products"]
              for s in p.get("sources", []) for im in s.get("images", [])
              if canonical_key(p, s, im)[0] == p["source_key"]
              and outcome(im)[0] == "accepted" and im.get("visual_reviewed")
              and im.get("eligible_for_cloud_without_card_id_review") is not False}

    # These are product-family proofs only. They never upgrade card identity.
    identity_path = batch / "research/identity-audit.json"
    identity = json.loads(identity_path.read_bytes()) if identity_path.exists() else {}
    product_audit = store.capture(str(identity_path), "review") if identity else None
    bowman_audit = identity.get("bowman_best_preview_audit", {})
    original_best_pdf = bowman_audit.get("source_pdf", {})
    best_pdf = store.capture(original_best_pdf.get("pdf_path"), "original_pdf",
                             original_best_pdf.get("pdf_sha256"))
    nbl_alias = next((deepcopy(im["product_binding"]["alias_evidence"])
                     for _, report, _ in results for p in report["products"]
                     if p["source_key"] == SINGLE_NBL for s in p["sources"] for im in s.get("images", [])
                     if isinstance(im.get("product_binding"), dict) and im["product_binding"].get("alias_evidence")), {})
    nbl_files = [store.capture(value, "bounded_web_reader") for key, value in nbl_alias.items()
                 if key.endswith("_reader")]

    assets, decisions, seen, source_decisions = [], [], {}, []
    input_counts, rebinding_counts, byte_rejections = Counter(), Counter(), Counter()
    source_products = set()
    for report_path, report, report_capture in results:
        cohort = report_path.parent.name
        for product in report["products"]:
            source_products.add(product["source_key"])
            for source_index, source in enumerate(product.get("sources", [])):
                source_copy = store.capture(source.get("capture_path"), source.get("capture_kind", "raw"), source.get("sha256"))
                fact_decision = source_fact_decision(product, source, source_copy)
                if fact_decision:
                    source_decisions.append(fact_decision)
                for image_index, image in enumerate(source.get("images", [])):
                    input_counts[cohort] += 1
                    key, correction = canonical_key(product, source, image)
                    if key not in products:
                        raise ValueError(f"unknown canonical source: {key}")
                    frozen = products[key]
                    attrs = frozen["attributes"]
                    decision, reason = outcome(image)
                    candidate_id = f"{cohort}:{product['source_key']}:{source_index}:{image_index}"
                    row = {"candidate_id": candidate_id, "source_review_id": image.get("review_id"),
                           "source_key": key, "collected_source_key": product["source_key"],
                           "title": frozen["canonical_name"], "cohort": cohort,
                           "source_url": source.get("url"), "image_url": image.get("image_url"),
                           "source_capture": source_copy, "source_report": report_capture,
                           "review_completed": True, "visual_reviewed": bool(image.get("visual_reviewed")),
                           "source_decision": decision, "decision": decision, "reason": reason,
                           "original_candidate": deepcopy(image), "asset_id": None}
                    evidence = [report_capture]
                    for contact in contact_paths(image):
                        item = store.capture(contact, "visual_review_contact_sheet")
                        if item:
                            evidence.append(item)
                    for name, expected in (("original_image_path", "original_image_sha256"),
                                           ("original_stream_path", "original_stream_sha256"),
                                           ("extracted_display_bytes_path", "extracted_display_bytes_sha256")):
                        if image.get(name):
                            item = store.capture(image[name], "original_embedded_or_captured_bytes", image.get(expected))
                            if item:
                                evidence.append(item)
                    row["review_evidence"] = evidence
                    path, info, image_copy = resolve(image.get("path"), batch), None, None
                    if path is None or not path.is_file():
                        row.update(decision="rejected", reason="Candidate has no captured image file; acquisition failure is closed without claiming a visual card review.")
                        byte_rejections["missing_path"] += 1
                    else:
                        data = path.read_bytes()
                        actual_hash = digest(data)
                        if image.get("sha256") and actual_hash != image["sha256"]:
                            raise ValueError(f"candidate bytes changed after review: {path}")
                        try:
                            info = decoded(data)
                        except (OSError, SyntaxError, ValueError) as exc:
                            row.update(decision="rejected", reason=f"Captured payload is not a decodable raster image ({type(exc).__name__}); source exclusion retained: {reason}")
                            byte_rejections["not_raster_image"] += 1
                        image_copy = store.image(path, data, info)
                        row.update(path=image_copy["local_path"], sha256=actual_hash,
                                   original_image_capture=image_copy, decoded_image=info)
                    if image.get("eligible_for_cloud_without_card_id_review") is False:
                        row.update(decision="rejected", reason="Retail product subset is not established by this Hobby launch autograph preview; eligible_for_cloud_without_card_id_review=false. Image was visually reviewed, but its product subset is unproven.")
                    if correction == "UNPROVEN_BOWMAN_BEST_SUBSET":
                        row.update(decision="rejected", reason="Bowman U Best product cohort is unproven for the calendar2024 source; only the independently audited2023-24 cohort is allowed there.")
                    if row["decision"] == "accepted" and not image.get("visual_reviewed"):
                        raise ValueError(f"accepted raster lacks actual visual review: {candidate_id}")
                    if row["decision"] == "accepted" and not source_copy:
                        raise ValueError(f"accepted image has no source capture: {candidate_id}")
                    if row["decision"] == "accepted":
                        assert image_copy and info
                        identity_key = (key, image_copy["sha256"])
                        if identity_key in existing:
                            row.update(decision="rejected", reason="duplicate_existing: identical source+sha256 already exists in frozen cloud baseline; accepted_as_same_asset preserves its existing asset without appending another.",
                                       duplicate_decision="duplicate_existing", accepted_as_same_asset=existing[identity_key], existing_asset_id=existing[identity_key])
                        elif key != product["source_key"] and identity_key in native:
                            same_asset = f"intake26:{key}:{image_copy['sha256'][:16]}"
                            row.update(decision="rejected", reason="duplicate_new: corrected product binding repeats the identical bytes in the correctly scoped source report; retain that native candidate as the sole accepted asset.",
                                       duplicate_decision="duplicate_new", accepted_as_same_asset=same_asset)
                        elif identity_key in seen:
                            row.update(decision="rejected", reason="duplicate_new: identical source+sha256 is already represented by one new accepted asset.",
                                       duplicate_decision="duplicate_new", accepted_as_same_asset=seen[identity_key])
                        else:
                            asset_id = f"intake26:{key}:{image_copy['sha256'][:16]}"
                            row["asset_id"] = asset_id
                            seen[identity_key] = asset_id
                            if key != product["source_key"]:
                                rebinding_counts[key] += 1
                            side = image.get("side")
                            composite = image.get("review_id") == "R45" or side == "front_and_back_composite"
                            side = side if side in {"front", "back"} and not composite else "unknown"
                            original_type = image.get("image_type", "")
                            physical = any(word in original_type for word in ("psa_cert_scan", "physical_card", "retailer_actual_card"))
                            kind = "thirdparty_card_scan" if physical else "official_design"
                            binding = {"frozen_product": {"source_key": key, "title": frozen["canonical_name"],
                                       "season_start": attrs.get("season_start"), "season_end": attrs.get("season_end"),
                                       "official_artifact": deepcopy(attrs.get("official_artifact"))},
                                       "baseline_evidence": baseline_evidence,
                                       "collected_product": {k: product.get(k) for k in ("source_key", "title", "season_start", "season_end")},
                                       "source_binding": deepcopy(source.get("product_binding")),
                                       "image_binding": deepcopy(image.get("product_binding")),
                                       "source_capture": source_copy, "canonical_seasons_unchanged": True}
                            if correction:
                                binding.update(cohort_correction=correction, independent_product_audit=product_audit)
                                if product["source_key"] == OLD_BEST:
                                    binding.update(original_calendar2024_checklist=best_pdf,
                                                   original_calendar2024_base16=deepcopy(bowman_audit.get("requested_number_16")))
                            if key == SINGLE_NBL:
                                binding.update(manufacturer_alias_bridge=nbl_alias, alias_bridge_captures=nbl_files,
                                               alias_rule="Manufacturer2022-23 / Product Year2023 / SKU FGC005267 / same NBL2302-2023NBLChromeChecklist.pdf bridge; frozen canonical source seasons remain2023/2023.")
                            source_identity = deepcopy(image.get("source_card_identity") or {})
                            source_identity.update({"player_as_source": deepcopy(image.get("player")),
                                                    "card_number_as_source": image.get("card_number"),
                                                    "variant_as_source": image.get("variant"),
                                                    "pair_evidence_as_source": deepcopy(image.get("pair_evidence")),
                                                    "source_key_as_collected": product["source_key"],
                                                    "product_title_as_collected": product["title"],
                                                    "season_start_as_collected": product.get("season_start"),
                                                    "season_end_as_collected": product.get("season_end"),
                                                    "identity_is_observation_only": True})
                            asset = {"asset_id": asset_id, "path": image_copy["local_path"], "sha256": image_copy["sha256"],
                                     "source_key": key, "title": frozen["canonical_name"], "publisher": attrs.get("manufacturer"),
                                     "season_start": attrs.get("season_start"), "season_end": attrs.get("season_end"),
                                     "side": side, "player": None, "card_number": None, "variant": None, "pair_id": None,
                                     "pair_evidence": None, "image_type": kind,
                                     "rendition": "whole_reference_composite" if composite else "slab_photo" if physical else "manufacturer_card_design",
                                     "source_url": source["url"], "image_url": image.get("image_url"),
                                     "source_authority": source.get("authority", "unknown"),
                                     "source_artifact_path": source_copy["local_path"], "source_artifact_sha256": source_copy["sha256"],
                                     "source_capture": source_copy,
                                     "review_artifact_path": report_capture["local_path"], "review_artifact_sha256": report_capture["sha256"],
                                     "review_basis": {"source_visual_review": deepcopy(image.get("review_basis")), "evidence": evidence,
                                                      "integration_candidate_id": candidate_id},
                                     "review_completed": True, "captured_at": image["captured_at"], "reviewed_at": now,
                                     "source_card_identity": source_identity, "identity_conflicts": deepcopy(image.get("identity_conflicts", [])),
                                     "product_binding_evidence": binding, "original_resolution": {k: info[k] for k in ("width", "height")},
                                     **info, "locator": json.dumps(image.get("locator"), ensure_ascii=False, sort_keys=True),
                                     "caption": f"{frozen['canonical_name']} — {side} source reference; exact card identity remains unassigned.",
                                     "status": "ready", "visibility": "public_reference", "usage_scope": "internal_reference_research",
                                     "rights": "Original source rights retained; source attribution and captured evidence preserved.",
                                     "parser_version": CONTRACT}
                            assets.append(asset)
                    row["accepted"] = row["decision"] == "accepted"
                    row["review_method"] = "source_visual_review_plus_local_integrity_and_product_binding_audit" if image.get("visual_reviewed") else "captured_payload_format_and_prior_exclusion_review"
                    decisions.append(row)
        # One early download is declared as an extra byte-identical artifact, not source.images.
        extra = report.get("summary", {}).get("extra_duplicate_artifact")
        if extra:
            path = resolve(extra["path"], batch)
            data = path.read_bytes()
            assert digest(data) == extra["sha256"]
            original = store.image(path, data, decoded(data))
            representative = next(row for row in decisions
                                  if row.get("cohort") == cohort and
                                  row.get("source_review_id") == extra.get("same_as"))
            decisions.append({"candidate_id": f"{cohort}:extra_duplicate_artifact", "cohort": cohort,
                              "source_review_id": extra.get("same_as"), "source_key": representative["source_key"],
                              "collected_source_key": representative["collected_source_key"],
                              "source_url": representative["source_url"], "source_capture": representative["source_capture"],
                              "source_report": report_capture, "review_completed": True, "visual_reviewed": True,
                              "decision": "rejected", "accepted": False, "reason": extra["reason"],
                              "duplicate_decision": "duplicate_artifact", "same_as_source_review": extra.get("same_as"),
                              "accepted_as_same_asset": representative.get("asset_id") or representative.get("accepted_as_same_asset"),
                              "path": original["local_path"], "sha256": original["sha256"], "original_image_capture": original})

    # Exercise the production attachment contract in memory, retaining all null identities.
    attach_checks = []
    for key in sorted({a["source_key"] for a in assets}):
        group = [a for a in assets if a["source_key"] == key]
        manifest = {"sources": [{"key": key}], "entities": [deepcopy(products[key])]}
        attached = attach_reference_images(manifest, group, media, asset_prefix="knowledge-intake-20261022/media")
        refs = attached["entities"][0]["attributes"]["reference_images"]
        assert len(refs) == len(group), key
        assert all(all(ref.get(field) is None for field in ("player", "card_number", "variant", "pair_id")) for ref in refs)
        assert attached["entities"][0]["attributes"]["knowledge_media_summary"]["paired_cards"] == 0
        attach_checks.append({"source_key": key, "attached_in_memory": len(refs)})
    assert len({(a["source_key"], a["sha256"]) for a in assets}) == len(assets)
    assert len({a["asset_id"] for a in assets}) == len(assets)
    assert len([r for r in decisions if r["decision"] == "accepted"]) == len(assets)
    assert all(row["accepted_as_same_asset"] in {a["asset_id"] for a in assets}
               for row in decisions if row.get("duplicate_decision") == "duplicate_new")
    by_product = Counter(a["source_key"] for a in assets)
    counts = [{"source_key": key, "title": products[key]["canonical_name"],
               "season_start": products[key]["attributes"].get("season_start"),
               "season_end": products[key]["attributes"].get("season_end"), "new_assets": by_product[key]}
              for key in sorted(source_products | set(by_product))]
    summary = {"contract": CONTRACT, "prepared_at": now, "baseline_version": baseline["version"],
               "baseline_checksum": baseline["checksum"], "baseline_sha256": digest(baseline_bytes),
               "source_image_candidates": dict(input_counts), "all_candidates": len(decisions),
               "new_assets": len(assets), "accepted_candidates": sum(r["accepted"] for r in decisions),
               "rejected_candidates": sum(not r["accepted"] for r in decisions),
               "duplicate_existing": sum(r.get("duplicate_decision") == "duplicate_existing" for r in decisions),
               "duplicate_new": sum(r.get("duplicate_decision") == "duplicate_new" for r in decisions),
               "byte_exclusions": dict(byte_rejections), "new_assets_by_type": dict(Counter(a["image_type"] for a in assets)),
               "new_assets_by_source": counts, "rebinding_new_assets": dict(rebinding_counts),
               "review_pending": 0, "identity_fields_unassigned": True, "paired_cards": 0,
               "production_attach_checks": attach_checks,
               "cloud_or_local_db_writes": False, "embedding_or_siglip_execution": False,
               "main_assets_jsonl_modified": False}
    source_decisions = list({(row["collected_source_key"], row["source_url"], row["source_capture_sha256"]): row
                             for row in source_decisions}.values())
    source_rebindings = [row for row in source_decisions
                         if row["sourcefacts_disposition"] == "rebind_to_existing_source"]
    summary.update(source_fact_rebindings=len(source_rebindings),
                   source_fact_exclusions=sum(row["sourcefacts_disposition"] ==
                                              "not_admitted_product_scope_ambiguous"
                                              for row in source_decisions))
    dump(audit, {"contract": CONTRACT, "summary": summary, "source_rebindings": source_rebindings,
                 "source_decisions": source_decisions, "source_decision_lookup":
                 "Match collected_source_key + source_url + source_capture_sha256; a reader capture hash can be shared by different certificate sections.",
                 "candidates": decisions})
    output = media / "new-assets.jsonl"
    output.write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in assets))
    dump(media / "new-assets-report.json", summary)
    dump(media / "new-evidence-manifest.json", {"files": list(store.files.values()),
                                               "new_assets_jsonl_sha256": digest(output.read_bytes()),
                                               "media_decisions_sha256": digest(audit.read_bytes())})
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, default=ROOT / "data/knowledge-intake-20261022")
    parser.add_argument("--baseline", type=Path, default=ROOT / ".runtime/knowledge-intake-20261022/cloud-baseline.json")
    args = parser.parse_args()
    batch = args.batch_root.resolve()
    paths = [batch / f"research/year{year}/result.json" for year in (2023, 2024, 2025)]
    report = prepare(batch, args.baseline.resolve(), paths)
    print(json.dumps({key: report[key] for key in ("all_candidates", "new_assets", "rejected_candidates",
                                                  "duplicate_existing", "duplicate_new", "new_assets_by_type",
                                                  "review_pending", "main_assets_jsonl_modified")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
