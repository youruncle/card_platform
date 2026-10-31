"""Validate captured public media before attaching it to an annual release."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("review timestamp must have a timezone")
    return parsed


def _verified_identity(asset: dict, cache: dict, official_artifact: dict) -> dict | None:
    proof = asset.get("identity_verification", {})
    if asset.get("image_type") != "verified_card_scan":
        return None
    if proof.get("status") != "verified":
        raise ValueError("verified scan requires card identity verification")
    if proof.get("artifact_sha256") != official_artifact.get("checksum"):
        raise ValueError("checklist checksum differs from product attachment")
    if (proof.get("source_url") != asset["source_url"]
            or proof.get("source_sha256") != asset["source_artifact_sha256"]
            or not proof.get("source_locator")):
        raise ValueError("card identity source proof differs from captured source")
    path = Path(proof["artifact_path"])
    cache_key = (str(path), proof["artifact_sha256"])
    if cache_key not in cache:
        data = path.read_bytes()
        if sha256(data).hexdigest() != proof["artifact_sha256"]:
            raise ValueError("checklist checksum differs from identity proof")
        cache[cache_key] = {c["id"]: c for c in json.loads(gzip.decompress(data))}
    ids = proof.get("candidate_card_ids") or [proof.get("card_id")]
    if not ids or any(not i or i not in cache[cache_key] for i in ids):
        raise ValueError("card identity is absent from official checklist")
    if proof.get("card_id") and (proof["card_id"] not in ids or len(ids) != 1):
        raise ValueError("card identity chosen ID conflicts with candidates")
    for card_id in ids:
        attrs = cache[cache_key][card_id]["attributes"]
        for field in ("source_key", "season_start", "season_end", "player", "card_number"):
            expected, observed = attrs.get(field), asset.get(field)
            if expected is None or observed is None or expected == "" or observed == "":
                raise ValueError(f"card identity missing: {field}")
            if field in {"player", "card_number"}:
                expected, observed = str(expected).casefold().strip(), str(observed).casefold().strip()
            if expected != observed or observed is None:
                raise ValueError(f"card identity disagrees with official checklist: {field}")
        if asset.get("variant") is not None and asset["variant"].casefold() not in {
            str(attrs.get(k) or "").casefold() for k in ("parallel", "source_variant_label", "section")
        }:
            raise ValueError("card identity variant disagrees with official checklist")
    if proof.get("scope") == "reviewed_unpaired_public_image":
        from card_platform.knowledge_intake_worker import verify_unpaired_export
        return verify_unpaired_export(asset, cache[cache_key][ids[0]]["attributes"])
    review_path = Path(asset.get("review_artifact_path") or "")
    review_key = (str(review_path), asset.get("review_artifact_sha256"))
    if review_key not in cache:
        data = review_path.read_bytes()
        if sha256(data).hexdigest() != review_key[1]:
            raise ValueError("identity review checksum differs from reviewed record")
        cache[review_key] = json.loads(data)
    capture = json.loads(Path(asset["source_artifact_path"]).read_bytes())
    evidence = asset.get("pair_evidence", {})
    # A reviewed single face has source identity, but cannot declare a pair.
    # Recover its record from the checksum-bound capture when pairing is absent.
    listing = (evidence or capture).get("record_type") == "public_specimen_listing"
    cert = (evidence.get("record_id") if evidence else
            capture.get("record_id") if listing else str(capture.get("cert") or ""))
    identity_field = "specimen_listing_id" if listing else "certification_number"
    reviews = [r for r in cache[review_key]["records"] if r.get(identity_field) == cert]
    if len(reviews) != 1:
        raise ValueError("identity review has no unique listing or certification record")
    review = reviews[0]
    source_identity = review.get("source_identity", {})
    if (review.get("status") not in {"core_identity_matched_variant_unresolved", "exact_official_section_matched"}
            or review.get("source_key") != asset["source_key"]
            or review.get("chosen_card_id") != proof.get("card_id")
            or set(review.get("candidate_card_ids", [])) != set(ids)
            or review.get("canonical_player") != asset["player"]
            or review.get("card_number") != asset["card_number"]
            or review.get("variant") != asset.get("variant")
            or source_identity.get("source_page_url") != asset["source_url"]
            or source_identity.get("source_artifact_sha256") != asset["source_artifact_sha256"]
            or not any(e.get("sha256") == asset["sha256"] and e.get("side") == asset["side"]
                       for e in review.get("image_evidence", []))):
        raise ValueError("card identity review does not bind this image, source and checklist candidate")
    if listing:
        serial = review.get("serial_evidence", {})
        number, total = serial.get("number"), serial.get("total")
        if (capture.get("record_type") != "public_specimen_listing" or capture.get("record_id") != cert
                or not isinstance(number, int) or not isinstance(total, int) or not 0 < number <= total
                or (asset.get("serial_number"), asset.get("serial_total")) != (number, total)
                or serial != capture.get("serial_evidence") or not serial.get("locator")
                or not serial.get("image_sha256")
                or not any(e.get("side") == "detail" and e.get("sha256") == serial["image_sha256"]
                           for e in review.get("image_evidence", []))
                or any(cache[cache_key][i]["attributes"].get("serial_total") != total for i in ids)):
            raise ValueError("listing identity requires a reviewed unique serial and official matching limit")
        detail_root = Path(asset["source_artifact_path"]).resolve().parent
        detail = (detail_root / str(serial.get("image_path") or "")).resolve()
        if (not serial.get("image_path") or not detail.is_relative_to(detail_root)
                or not serial.get("image_url")
                or not any(e.get("url") == serial["image_url"] for e in capture.get("observed_link_bindings", []))):
            raise ValueError("listing serial closeup requires a sealed relative path and same-listing URL")
        try:
            if sha256(detail.read_bytes()).hexdigest() != serial["image_sha256"]:
                raise ValueError("listing serial closeup checksum mismatch")
            from PIL import Image
            with Image.open(detail) as image:
                image.verify()
        except OSError as exc:
            raise ValueError("listing serial closeup is missing or undecodable") from exc
    source_record_id = capture.get("record_id") if listing else capture.get("cert")
    if (str(source_record_id) != cert or capture.get("page_url") != asset["source_url"]
            # The source proves the URL, while reviewed image hashes above prove
            # orientation. Link ordinal by itself is never side evidence.
            or not any(e.get("url") == asset["image_url"]
                       for e in capture.get("observed_link_bindings", []))):
        raise ValueError("card identity source capture does not bind the certification image URL")
    return {key: deepcopy(value) for key, value in proof.items() if key != "artifact_path"}


def attach_reference_images(manifest: dict, assets: list[dict], media_root: Path,
                            *, now: datetime | None = None,
                            asset_prefix: str = "knowledge-three-seasons/media") -> dict:
    """Copy a manifest and add checksum-verified, reviewed source design images.

    A source design does not establish a player, card number, variant or pair.
    Private, unreviewed and unrelated records never enter the public release.
    """
    result = deepcopy(manifest)
    prefix = PurePosixPath(asset_prefix)
    if prefix.is_absolute() or ".." in prefix.parts or len(prefix.parts) != 2 or prefix.parts[-1] != "media":
        raise ValueError("asset prefix must be a batch/media path inside the data namespace")
    keys = {source["key"] for source in manifest.get("sources", [])}
    products = [e for e in result.get("entities", []) if e.get("entity_type") == "product"]
    if len(products) != 1:
        raise ValueError("release requires exactly one product")
    attrs = products[0].setdefault("attributes", {})
    root = Path(media_root).resolve()
    current = now or datetime.now(timezone.utc)
    references = []
    identity_cache, pair_candidates = {}, {}
    seen = set()
    for asset in assets:
        if (asset.get("source_key") not in keys or asset.get("status") != "ready"
                or asset.get("visibility") != "public_reference"):
            continue
        if any(asset.get(k) != attrs.get(k) for k in ("season_start", "season_end")):
            raise ValueError("asset season differs from product season")
        try:
            captured, reviewed = (_timestamp(asset[k]) for k in ("captured_at", "reviewed_at"))
            if not captured <= reviewed <= current:
                raise ValueError("review timestamp is outside capture/current time")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid review timestamp: {exc}") from exc
        path = Path(asset["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("image path is outside media root")
        if sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            raise ValueError("image checksum differs from reviewed bytes")
        source = Path(asset["source_artifact_path"])
        if sha256(source.read_bytes()).hexdigest() != asset["source_artifact_sha256"]:
            raise ValueError("source checksum differs from captured bytes")
        identity = (asset["sha256"], asset["source_key"], asset.get("locator"))
        if identity in seen:
            continue
        seen.add(identity)
        reference = {k: asset.get(k) for k in (
            "asset_id", "sha256", "source_key", "source_url", "image_url", "source_authority",
            "source_artifact_sha256", "locator", "caption", "captured_at", "reviewed_at",
            "image_type", "usage_scope", "rights", "product_binding_evidence",
            "source_card_identity", "identity_conflicts", "rendition", "original_resolution",
            "review_artifact_sha256",
        )}
        reference.update(asset_path=prefix.as_posix() + "/" + path.relative_to(root).as_posix(),
                         side=asset.get("side", "unknown"), player=None, card_number=None,
                         variant=None, pair_id=None)
        verified = _verified_identity(asset, identity_cache, attrs.get("official_artifact", {}))
        if verified:
            reference.update(player=asset["player"], card_number=asset["card_number"],
                             variant=asset.get("variant"), identity_verification=verified)
            if asset.get("pair_evidence", {}).get("record_type") == "public_specimen_listing":
                reference.update(serial_number=asset["serial_number"], serial_total=asset["serial_total"])
            evidence = asset.get("pair_evidence", {})
            if (asset.get("pair_id") and evidence.get("record_type") in {"certification", "public_specimen_listing"}
                    and evidence.get("record_id") and evidence.get("record_url") == asset["source_url"]):
                pair_candidates.setdefault(asset["pair_id"], []).append((reference, evidence))
        references.append(reference)
    pairs = 0
    for pair_id, candidates in pair_candidates.items():
        faces = [r for r, _ in candidates]
        if len(faces) != 2 or {r["side"] for r in faces} != {"front", "back"}:
            continue
        fields = ("source_key", "player", "card_number", "variant", "source_url", "source_artifact_sha256")
        if (faces[0]["sha256"] == faces[1]["sha256"]
                or any(faces[0][k] != faces[1][k] for k in fields)
                or faces[0]["identity_verification"] != faces[1]["identity_verification"]
                or candidates[0][1] != candidates[1][1]):
            continue
        for face, evidence in candidates:
            face.update(pair_id=pair_id, pair_evidence=deepcopy(evidence))
        pairs += 1
    attrs["reference_images"] = references
    attrs["knowledge_media_summary"] = {
        "reference_images": len(references), "paired_cards": pairs,
        "verified_card_images": sum("identity_verification" in r for r in references),
        "design_reference_images": sum(r["image_type"] != "verified_card_scan" for r in references),
        "coverage_basis": "reviewed source samples; whole-set image coverage unknown",
    }
    return result
