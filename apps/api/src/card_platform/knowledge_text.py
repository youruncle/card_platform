"""Compile captured official checklists into bounded, auditable knowledge packages.

This module reads local captures only. It does not fetch, publish, or change cards.
Each artifact contains only the card variants explicitly present in its document.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

from card_platform.knowledge_manifest import validate_manifest
from card_platform.official_checklists import (
    PARSER_VERSION,
    build_manifest,
    isolate_conflicting_rows,
    official_url,
    parse_document,
    parse_mercury_pages,
    product_fields,
)


COMPILER_VERSION = "official-text-compiler-v1"
FACT_FIELDS = (
    "product", "season_start", "season_end", "brand", "sport", "player",
    "card_number", "team", "insert", "section", "parallel", "serial_total",
    "source_variant_label",
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def _identity(attributes: dict) -> tuple:
    return tuple(attributes.get(k) for k in ("section", "card_number", "player"))


def _variant_identity(attributes: dict) -> tuple:
    return (*_identity(attributes), attributes.get("source_variant_label"))


def parse_uconn_pages(pages: list[str]) -> tuple[list[dict], list[dict]]:
    """Read this official checkbox checklist's logical text, preserving row markers."""
    sections = {
        "COLLEGE CONTENDERS", "LEGENDARY CONTENDERS", "SCHOOL COLORS", "CAMPUS LEGENDS",
        "PRIZM", "PRIZM STUDENT ORIENTATION", "PRIZM FEARLESS", "PRIZM FIREWORKS",
        "ON CAMPUS", "COLOR BLAST", "THUNDER", "PROFILES", "TROPHY HUNTING",
        "PRIVATE SIGNINGS", "PINNACLE MASCOT",
    }
    rows, issues, headings = [], [], []
    counts = Counter()
    section = None
    for page, text in enumerate(pages, 1):
        for line_number, raw_text in enumerate(text.splitlines(), 1):
            value = " ".join(raw_text.split())
            if not value or value.startswith((
                "2025 PANINI UNIVERSITY OF CONNECTICUT", "All information is accurate",
                "images are solely", "determined. © 2025 Panini",
            )):
                continue
            if value in sections:
                section = value
                headings.append((section, page, line_number))
                continue
            match = re.fullmatch(r"\s*([A-Z0-9]+(?:-[A-Z0-9]+)*)\s+(.+?)(?:\s+(SSP|SP))?", value)
            if not match or not section:
                issues.append({"page": page, "line": line_number, "text": raw_text,
                               "reason": "unresolved_uconn_checklist_line"})
                continue
            number, player, marker = match.groups()
            row = {
                "card_number": number, "player": player, "team": None,
                "section": section, "insert": section.title(),
                "source_page": page, "source_line": line_number, "raw_text": raw_text,
            }
            if marker:
                row["source_variant_label"] = marker
            rows.append(row)
            counts[section] += 1
    issues.extend({"page": page, "line": line, "text": section,
                   "reason": "section_without_card_rows"}
                  for section, page, line in headings if not counts[section])
    return rows, issues


def compare_card_facts(cards: list[dict], previous: list[dict], *,
                       normalize_participant_order=True) -> dict[str, int]:
    """Compare semantic card facts, ignoring capture/parser/evidence-only changes."""
    def facts(card):
        attrs = dict(card["attributes"])
        # Official multi-subject exports can reorder the same names/teams.
        # No positional subject/team mapping is asserted by this schema.
        if normalize_participant_order:
            for field in ("player", "team"):
                value = attrs.get(field)
                if isinstance(value, str) and "/" in value:
                    attrs[field] = tuple(sorted(part.strip() for part in value.split("/")))
        return attrs

    current = {_variant_identity(attrs): attrs for attrs in map(facts, cards)}
    old = {_variant_identity(attrs): attrs for attrs in map(facts, previous)}
    shared = current.keys() & old.keys()
    changed = sum(
        any(current[key].get(field) != old[key].get(field) for field in FACT_FIELDS)
        for key in shared
    )
    return {
        "new": len(current.keys() - old.keys()),
        "changed": changed,
        "unchanged": len(shared) - changed,
        "removed": len(old.keys() - current.keys()),
    }


def compile_source(product: dict, receipt: dict, output_root: str | Path) -> dict:
    """Verify and compile one source; corrupt captures never emit new manifests."""
    key = product["source_key"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise ValueError("Unsafe official source key")
    for field in ("source_key", "title", "publisher", "source_url"):
        if receipt.get(field) != product.get(field):
            raise ValueError(f"Capture receipt {field} does not match inventory")
    if receipt.get("status") != "downloaded" or receipt.get("http_status") != 200:
        raise ValueError("Source was not successfully captured")
    if not official_url(product["source_url"]):
        raise ValueError("Source is not an allowed official document origin")
    if not receipt.get("captured_at"):
        raise ValueError("Capture receipt has no timestamp")
    raw = Path(receipt["path"])
    payload = raw.read_bytes()
    checksum = sha256(payload).hexdigest()
    if checksum != receipt.get("sha256"):
        raise ValueError("Source checksum does not match capture receipt")
    if len(payload) != receipt.get("bytes"):
        raise ValueError("Source byte count does not match capture receipt")
    root = Path(output_root).resolve()
    fields = product_fields(product["title"])
    for field in ("season_start", "season_end"):
        if product.get(field) is not None and product[field] != fields[field]:
            raise ValueError(f"Official title {field} does not match inventory")
    parse_error = None
    logical_lines = False
    try:
        rows, issues, pages = parse_document(raw)
        # This official source uses a distinct, explicitly labelled two-column layout.
        if product["title"] == "2023-24 Topps Mercury Victor Wembanyama Basketball":
            rows, issues = parse_mercury_pages(pages)
        if key == "panini-api-2025-1507" and payload.startswith(b"%PDF"):
            from pypdf import PdfReader

            pages = [page.extract_text() for page in PdfReader(raw).pages]
            if not all("2025 PANINI UNIVERSITY OF CONNECTICUT" in page for page in pages):
                raise ValueError("Unexpected UConn official PDF header")
            rows, issues = parse_uconn_pages(pages)
            logical_lines = True
    except Exception as exc:
        parse_error = str(exc)
        rows, pages = [], []
        issues = [{"reason": "document_parse_error", "text": parse_error}]
    parsed_rows = rows
    rows, conflicts = isolate_conflicting_rows(rows)
    issues = [*issues, *conflicts]
    status = "parse_failed" if parse_error else "no_rows" if not rows else "partial" if issues else "compiled"
    source = {
        **product,
        "key": key,
        "checksum": checksum,
        "retrieved_at": receipt["captured_at"],
    }
    manifest = build_manifest(source, rows)
    if any(row.get("source_variant_label") for row in rows):
        # Same printed card number/player can occur both with and without SP.
        # Preserve these explicit rows without inventing a parallel or serial.
        entities = [e for e in manifest["entities"] if e["entity_type"] == "product"]
        for row in rows:
            entity = next(e for e in build_manifest(source, [row])["entities"]
                          if e["entity_type"] == "card")
            if marker := row.get("source_variant_label"):
                entity["attributes"]["source_variant_label"] = marker
                entity["canonical_name"] += " · " + marker
                entity["key"] = sha256(("card" + entity["canonical_name"]).encode()).hexdigest()[:24]
            entities.append(entity)
        manifest["entities"] = entities
    source_filename = unquote(Path(urlparse(product["source_url"]).path).name)
    provenance = {
        "source_key": key,
        "source_checksum": checksum,
        "source_filename": raw.name,
        "original_source_filename": source_filename,
        "compiler_version": COMPILER_VERSION,
        **({"source_locator_basis": "pypdf_logical_text_lines"} if logical_lines else {}),
    }
    locators = defaultdict(list)
    for row in rows:
        locators[_variant_identity(row)].append({
            "source_page": row["source_page"],
            "source_line": row["source_line"],
            "raw_text": row["raw_text"],
        })
    cards = []
    for entity in manifest["entities"]:
        if entity["entity_type"] != "card":
            continue
        attrs = entity["attributes"]
        attrs.update(provenance)
        attrs["source_locators"] = locators[_variant_identity(attrs)]
        cards.append({
            "id": "official:" + key + ":" + entity["key"],
            "entity_type": "card",
            "canonical_name": entity["canonical_name"],
            "attributes": attrs,
        })
    artifact = None
    artifact_root = root / "artifacts"
    if cards:
        blob = gzip.compress(
            json.dumps(cards, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0,
        )
        digest = sha256(blob).hexdigest()
        artifact_path = artifact_root / f"{digest}.json.gz"
        artifact_root.mkdir(parents=True, exist_ok=True)
        if artifact_path.exists():
            if sha256(artifact_path.read_bytes()).hexdigest() != digest:
                raise ValueError("Existing immutable artifact checksum mismatch")
        else:
            artifact_path.write_bytes(blob)
        artifact = {
            "source_key": key, "path": artifact_path.name, "checksum": digest,
            "card_count": len(cards), **fields,
        }
    sections = Counter(c["attributes"]["section"] for c in cards)
    variant_fields = ("section", "insert", "parallel", "serial_total")
    if any(c["attributes"].get("source_variant_label") for c in cards):
        variant_fields = (*variant_fields, "source_variant_label")
    variants = Counter(tuple(c["attributes"].get(k) for k in variant_fields) for c in cards)
    evidence = {
        "status": status,
        "complete_for_captured_source": bool(cards) and not issues,
        "completeness_scope": "captured_document_only",
        "parsed_row_count": len(parsed_rows),
        "card_fact_count": len(cards),
        "quarantined_rows": len(issues),
        "section_counts": dict(sorted(sections.items())),
        "explicit_variant_counts": [
            dict(zip(variant_fields, variant)) | {"card_count": count}
            for variant, count in sorted(
                variants.items(), key=lambda item: tuple(str(v) for v in item[0])
            )
        ],
        **({"sections_without_card_rows": [i["text"] for i in issues
                                            if i["reason"] == "section_without_card_rows"]}
           if any(i["reason"] == "section_without_card_rows" for i in issues) else {}),
    }
    manifest["entities"] = [e for e in manifest["entities"] if e["entity_type"] == "product"]
    attrs = manifest["entities"][0]["attributes"]
    attrs.update(provenance)
    attrs["text_evidence"] = evidence
    if artifact:
        attrs["official_artifact"] = artifact
    metadata = manifest["sources"][0]["metadata"]
    metadata.update({
        **provenance,
        "capture_receipt": receipt,
        "text_status": status,
        "quarantined_rows": len(issues),
        "release_date_basis": product.get("date_basis"),
    })
    for field in ("source_pages", "recovery_basis"):
        if field in product:
            metadata[field] = product[field]
    report = validate_manifest(manifest)
    if not report.publishable:
        raise ValueError(f"Invalid compiled source manifest: {report.conflicts}")
    parsed_path = root / "parsed" / f"{key}.json"
    manifest_path = root / "manifests" / f"{key}.json"
    _write_json(parsed_path, {
        "source_key": key, "source_url": product["source_url"],
        "source_filename": raw.name, "checksum": checksum,
        "parser_version": PARSER_VERSION, "compiler_version": COMPILER_VERSION,
        "parsed_rows": parsed_rows, "rows": rows, "issues": issues,
        "conflicts": conflicts, "pages": pages, "capture_receipt": receipt,
    })
    _write_json(manifest_path, manifest)
    return {
        "source_key": key, "title": product["title"], "publisher": product["publisher"],
        **fields, "status": status, "source_checksum": checksum,
        "raw_path": str(raw.resolve()), "parsed_path": str(parsed_path),
        "manifest_path": str(manifest_path), "manifest_checksum": report.checksum,
        "artifact": artifact, "artifact_root": str(artifact_root),
        "card_fact_count": len(cards), "quarantined_rows": len(issues),
        "section_count": len(sections), "explicit_variant_count": len(variants),
        "issue_reasons": dict(sorted(Counter(i["reason"] for i in issues).items())),
        **({"error": parse_error} if parse_error else {}),
    }


def _previous_cards(product: dict, baseline_root: Path) -> list[dict]:
    artifact = product.get("artifact")
    if artifact:
        path = baseline_root / "artifacts" / artifact["path"]
        blob = path.read_bytes()
        if sha256(blob).hexdigest() != artifact["checksum"]:
            raise ValueError("Baseline artifact checksum mismatch")
        return json.loads(gzip.decompress(blob))
    manifest_path = baseline_root / "manifests" / f"{product['key']}.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        return [e for e in manifest["entities"] if e["entity_type"] == "card"]
    return []


def compile_inventory(inventory: dict, text_root: str | Path, *, baseline_root=None,
                      progress=None) -> dict:
    """Resume all captured sources; each failed or absent capture stays visible."""
    root = Path(text_root).resolve()
    baseline_root = Path(baseline_root) if baseline_root else None
    previous = {}
    if baseline_root and (baseline_root / "catalog.json").exists():
        catalog = json.loads((baseline_root / "catalog.json").read_text())
        previous = {p["key"]: p for p in catalog["products"]}
    results = []
    for product in inventory["products"]:
        key = product["source_key"]
        result = {"source_key": key, "title": product["title"], "publisher": product["publisher"]}
        try:
            receipt_path = root / "receipts" / f"{key}.json"
            if not receipt_path.exists():
                result.update(status="missing_capture", error="Capture receipt not available")
            else:
                receipt = json.loads(receipt_path.read_text())
                result = compile_source(product, receipt, root)
                if baseline_root:
                    try:
                        old_cards = _previous_cards(previous.get(key, {"key": key}), baseline_root)
                        artifact = result["artifact"]
                        cards = json.loads(gzip.decompress(
                            (root / "artifacts" / artifact["path"]).read_bytes()
                        )) if artifact else []
                        result["semantic_delta"] = compare_card_facts(cards, old_cards)
                        result["source_representation_delta"] = compare_card_facts(
                            cards, old_cards, normalize_participant_order=False,
                        )
                        result["baseline_card_count"] = len(old_cards)
                        result["baseline_source_checksum"] = previous.get(key, {}).get("checksum")
                    except Exception as exc:
                        result["semantic_delta"] = None
                        result["baseline_error"] = str(exc)
        except Exception as exc:
            result.update(status="failed", error=str(exc))
        results.append(result)
        if progress:
            progress(len(results), len(inventory["products"]), result)
    statuses = Counter(p["status"] for p in results)
    report = {
        "contract": "official-text-compile-v1", "compiler_version": COMPILER_VERSION,
        "parser_version": PARSER_VERSION,
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "discovery_complete": bool(inventory.get("discovery_complete")),
        "scope": "captured officially discovered sources; no exhaustive annual coverage claim",
        "summary": {
            "source_count": len(results), "statuses": dict(sorted(statuses.items())),
            "sources_with_manifests": sum(bool(r.get("manifest_path")) for r in results),
            "sources_with_artifacts": sum(bool(r.get("artifact")) for r in results),
            "card_fact_count": sum(r.get("card_fact_count", 0) for r in results),
            "quarantined_rows": sum(r.get("quarantined_rows", 0) for r in results),
            "semantic_delta": {
                field: sum((r.get("semantic_delta") or {}).get(field, 0) for r in results)
                for field in ("new", "changed", "unchanged", "removed")
            } if baseline_root else None,
            "source_representation_delta": {
                field: sum((r.get("source_representation_delta") or {}).get(field, 0) for r in results)
                for field in ("new", "changed", "unchanged", "removed")
            } if baseline_root else None,
            "baseline_comparison_failures": sum(bool(r.get("baseline_error")) for r in results),
        },
        "products": results,
        "official_artifacts": [r["artifact"] for r in results if r.get("artifact")],
    }
    _write_json(root / "compile-report.json", report)
    return report
