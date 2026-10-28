"""Validated, local-only inputs and deterministic extraction for a read-only plan."""
from __future__ import annotations

import gzip
import json
import re
import shutil
import sys
import tempfile
import zlib
from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

from card_platform.knowledge import snapshot_checksum
from card_platform.knowledge_text import COMPILER_VERSION, compile_source
from card_platform.official_checklists import PARSER_VERSION, build_manifest, product_fields


class InputContractError(ValueError):
    """Fatal input contract or filesystem boundary violation."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def issue(code: str, *, input_id=None, product_source_key=None, detail="", recovery_action=None):
    return {"code": code, "input_id": input_id, "product_source_key": product_source_key,
            "detail": detail, "recovery_action": recovery_action or "Provide verified local evidence and rerun."}


def strict_json(data: bytes | str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InputContractError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid(value):
        raise InputContractError("Non-finite JSON number")

    try:
        value = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data,
                           object_pairs_hook=pairs, parse_constant=invalid)
        canonical(value)  # Also rejects overflow such as 1e999.
        return value
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise InputContractError("Invalid UTF-8 JSON contract") from exc


def input_path(root: Path, relative: str | Path) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not str(path) or str(path) == ".":
        raise InputContractError("Input path must be relative and contained")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise InputContractError("Input path escapes input root")
    return resolved


def validate_output_paths(input_root: Path, output_root: Path, cache_root: Path) -> None:
    paths = [p.absolute() for p in (input_root, output_root, cache_root)]
    # A write root or one of its parents must not redirect writes via a symlink.
    for path in paths[1:]:
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise InputContractError("Write root cannot traverse symlinks")
        if path.exists() and not path.is_dir():
            raise InputContractError("Write root must be a directory")
        if path.exists() and any(p.is_symlink() for p in path.rglob("*")):
            raise InputContractError("Write directory contains a symlink")
    resolved = [p.resolve() for p in paths]
    for i, first in enumerate(resolved):
        for second in resolved[i + 1:]:
            if first.is_relative_to(second) or second.is_relative_to(first):
                raise InputContractError("Input, output and cache directories must be disjoint")


def _object(value, contract):
    if not isinstance(value, dict) or value.get("contract") != contract:
        raise InputContractError(f"Expected {contract}")


def _list(value, name):
    if not isinstance(value, list):
        raise InputContractError(f"Expected list: {name}")
    return value


def _mapping(value, name):
    if not isinstance(value, dict):
        raise InputContractError(f"Expected object: {name}")
    return value


def _records(value, name):
    records = _list(value, name)
    for record in records:
        _mapping(record, name)
    return records


def _facts(value):
    records = _records(value, "facts")
    for fact in records:
        if not isinstance(fact.get("field"), str) or not fact["field"] or "value" not in fact:
            raise InputContractError("Fact must contain field and value")
        if "subject_key" in fact and not isinstance(fact["subject_key"], str):
            raise InputContractError("Fact subject key must be a string")
    return records


def _attributes(value):
    attrs = _mapping(value, "entity attributes")
    for field in ("source_key", "manufacturer", "product", "sport", "source_url"):
        if field in attrs and attrs[field] is not None and not isinstance(attrs[field], str):
            raise InputContractError("Invalid product binding field")
    for evidence in _records(attrs.get("supplementary_evidence", []), "supplementary evidence"):
        _facts(evidence.get("facts", []))
        for field in ("source_url", "source_sha256"):
            if field in evidence and evidence[field] is not None and not isinstance(evidence[field], str):
                raise InputContractError("Invalid supplementary provenance")
    _records(attrs.get("reference_images", []), "reference images")
    return attrs


def _unique_strings(value, name):
    values = _list(value, name)
    if any(not isinstance(v, str) or not v for v in values) or len(values) != len(set(values)):
        raise InputContractError(f"Invalid or duplicate {name}")
    return values


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def load_inputs(input_root: Path, baseline_path: Path, scope_path: Path,
                captures_path: Path) -> dict[str, Any]:
    root = input_root.resolve()
    hashes = {}

    def read(relative):
        path = input_path(root, relative)
        data = path.read_bytes()
        hashes[str(Path(relative))] = sha256(data).hexdigest()
        return data

    try:
        baseline, scope, captures = [strict_json(read(p)) for p in
                                     (baseline_path, scope_path, captures_path)]
    except OSError as exc:
        raise InputContractError("Required input file unavailable") from exc
    _object(baseline, "knowledge-readonly-baseline-v1")
    _object(scope, "knowledge-intake-plan-scope-v1")
    _object(captures, "knowledge-source-inputs-v1")
    snapshot = baseline.get("snapshot")
    if not isinstance(snapshot, dict) or any(not isinstance(snapshot.get(k), list)
                                            for k in ("entities", "relations", "official_artifacts")):
        raise InputContractError("Complete snapshot required")
    published = baseline.get("published", {})
    if (not isinstance(published, dict) or not _digest(published.get("checksum"))
            or snapshot_checksum(snapshot) != published["checksum"]):
        raise InputContractError("Snapshot checksum mismatch")
    if (type(published.get("version")) is not int or not published.get("version_id")
            or not isinstance(baseline.get("captured_at"), str)
            or baseline.get("workspace_revision") is not None
            and type(baseline["workspace_revision"]) is not int):
        raise InputContractError("Invalid baseline identity")
    baseline.setdefault("coverage_registry", {"status": "unavailable", "rows": []})
    registry = baseline["coverage_registry"]
    if not isinstance(registry, dict) or registry.get("status") not in {"available", "unavailable"}:
        raise InputContractError("Invalid coverage registry")
    _list(registry.get("rows"), "registry rows")
    if "sources" in baseline:
        for source in _records(baseline["sources"], "sources"):
            if "metadata" in source:
                _mapping(source["metadata"], "source metadata")
    products = {}
    for entity in snapshot["entities"]:
        if not isinstance(entity, dict):
            raise InputContractError("Invalid snapshot entity")
        attrs = _attributes(entity.get("attributes", {}))
        if entity.get("entity_type") != "product" or not attrs.get("source_key"):
            continue
        key = attrs["source_key"]
        if key in products:
            raise InputContractError("Duplicate product source_key")
        products[key] = entity
    keys = _unique_strings(scope.get("existing_product_keys"), "scope keys")
    _unique_strings(scope.get("annual_research_keys"), "annual research keys")
    seasons = _list(scope.get("season_starts"), "season starts")
    if any(type(y) is not int or y not in {2023, 2024, 2025} for y in seasons) or len(seasons) != len(set(seasons)):
        raise InputContractError("Invalid season scope")
    for key in keys:
        attrs = products.get(key, {}).get("attributes", {})
        if (attrs.get("manufacturer") not in {"Topps", "Panini"}
                or any(attrs.get(k) in (None, "") for k in
                       ("product", "sport", "season_start", "season_end"))
                or type(attrs.get("season_start")) is not int
                or type(attrs.get("season_end")) is not int
                or attrs["season_start"] not in seasons):
            raise InputContractError("Scope must exactly bind a complete official product")
    descriptors = defaultdict(list)
    for artifact in snapshot["official_artifacts"]:
        if not isinstance(artifact, dict) or not artifact.get("source_key"):
            raise InputContractError("Invalid artifact descriptor")
        descriptors[artifact["source_key"]].append(artifact)
    files = {}
    for record in _list(baseline.get("artifact_files"), "artifact files"):
        if not isinstance(record, dict) or not _digest(record.get("checksum")):
            raise InputContractError("Invalid artifact file record")
        input_path(root, record["path"])
        if record["checksum"] in files and files[record["checksum"]] != record["path"]:
            raise InputContractError("Ambiguous artifact file mapping")
        files[record["checksum"]] = record["path"]
    cards, card_counts, problems = {}, {}, []
    for key, records in sorted(descriptors.items()):
        unique = {canonical(r): r for r in records}
        code = None
        if len(unique) != 1:
            code = "conflicting_artifact_descriptors"
        else:
            artifact = next(iter(unique.values()))
            relative = files.get(artifact.get("checksum"))
            if not relative:
                code = "missing_artifact"
            else:
                try:
                    blob = read(relative)
                    if sha256(blob).hexdigest() != artifact.get("checksum"):
                        code = "artifact_checksum_mismatch"
                    else:
                        rows = strict_json(gzip.decompress(blob))
                        if (not isinstance(rows, list) or type(artifact.get("card_count")) is not int
                                or len(rows) != artifact["card_count"]
                                or any(not isinstance(c, dict) or c.get("entity_type") != "card"
                                       or not isinstance(c.get("attributes"), dict)
                                       or c["attributes"].get("source_key") != key for c in rows)):
                            code = "artifact_shape_mismatch"
                        else:
                            card_counts[key] = len(rows)
                            if key in keys:
                                cards[key] = rows
                except OSError:
                    code = "missing_artifact"
                except (ValueError, EOFError, zlib.error):
                    code = "artifact_shape_mismatch"
        if code:
            problems.append(issue(code, product_source_key=key))
    items = _list(captures.get("items"), "captures items")
    ids = []
    for item in items:
        if not isinstance(item, dict) or item.get("product_source_key") not in keys:
            raise InputContractError("Capture must bind a frozen product source_key")
        ids.append(item.get("input_id"))
        if any(k in item and not isinstance(item[k], dict) for k in ("receipt", "product")):
            raise InputContractError("Capture product and receipt must be objects")
        if not isinstance(item.get("adapter"), str):
            raise InputContractError("Capture adapter required")
        for relative in [item.get("facts_path"), item.get("receipt", {}).get("path")]:
            if relative is not None:
                path = input_path(root, relative)
                if path.is_file():
                    hashes[str(Path(relative))] = sha256(path.read_bytes()).hexdigest()
    _unique_strings(ids, "input IDs")
    return {"baseline": baseline, "scope": scope, "items": deepcopy(items),
            "input_hashes": hashes, "baseline_cards": cards,
            "baseline_card_counts": card_counts,
            "baseline_artifact_issues": problems, "input_root": root, "products": products}


ADAPTER_VERSIONS = {"official_document_v1": "official-adapter-v1",
                    "specialist_facts_v1": "specialist-adapter-v1"}


def extraction_cache_key(item: dict[str, Any]) -> str:
    dependencies = {"adapter": item["adapter"],
                    "adapter_version": ADAPTER_VERSIONS.get(item["adapter"]),
                    "parser_version": PARSER_VERSION, "compiler_version": COMPILER_VERSION,
                    "product_source_key": item["product_source_key"],
                    "product": item.get("product"), "bound_scope": item.get("_bound_scope"),
                    "receipt": item.get("receipt"), "extraction_parameters": item.get("parameters", {}),
                    "input_hashes": item.get("_input_hashes", {})}
    return sha256(canonical(dependencies).encode()).hexdigest()


def _prepare(item, inputs):
    root = inputs["input_root"]
    item = deepcopy(item)
    attrs = inputs["products"][item["product_source_key"]]["attributes"]
    item["_bound_scope"] = {k: attrs.get(k) for k in
                            ("source_key", "manufacturer", "product", "sport", "season_start", "season_end")}
    item["_input_hashes"] = {}

    def read(relative):
        path = input_path(root, relative)
        blob = path.read_bytes()
        digest = sha256(blob).hexdigest()
        item["_input_hashes"][str(relative)] = digest
        inputs["input_hashes"][str(relative)] = digest
        return blob

    adapter = item["adapter"]
    if adapter not in ADAPTER_VERSIONS:
        return item, "unsupported_adapter"
    if adapter == "official_document_v1":
        product, receipt = item.get("product", {}), item.get("receipt", {})
        if (not isinstance(attrs.get("sport"), str)
                or attrs["sport"].strip().casefold() != "basketball"
                or "sport" in product and (not isinstance(product["sport"], str)
                    or product["sport"].strip().casefold() != "basketball")
                or product.get("source_key") != item["product_source_key"]
                or product.get("publisher") != attrs["manufacturer"]
                or any(receipt.get(k) != product.get(k) for k in
                       ("source_key", "title", "publisher", "source_url"))):
            return item, "capture_product_mismatch"
        if receipt.get("http_status") != 200 or receipt.get("status") != "downloaded":
            return item, "capture_http_error"
        if not receipt.get("captured_at") or not receipt.get("path"):
            return item, "missing_capture"
        try:
            fields = product_fields(product.get("title", ""))
            if any(fields.get(k) != attrs.get(k) for k in
                   ("product", "season_start", "season_end")) or any(
                       product.get(k) is not None and product[k] != attrs[k]
                       for k in ("season_start", "season_end")):
                return item, "capture_product_mismatch"
            data = read(receipt["path"])
        except OSError:
            return item, "missing_capture"
        except ValueError as exc:
            if isinstance(exc, InputContractError):
                raise
            return item, "capture_product_mismatch"
        if sha256(data).hexdigest() != receipt.get("sha256"):
            return item, "capture_checksum_mismatch"
        if len(data) != receipt.get("bytes"):
            return item, "capture_size_mismatch"
        item["_source_url"], item["_source_sha256"] = product["source_url"], receipt["sha256"]
    else:
        try:
            document = strict_json(read(item.get("facts_path", "")))
            records = document.get("products") if isinstance(document, dict) else None
            if not isinstance(records, list):
                return item, "invalid_specialist_facts"
            matches = [(i, r) for i, r in enumerate(records)
                       if isinstance(r, dict) and r.get("source_key") == item["product_source_key"]]
            if len(matches) != 1:
                return item, "ambiguous_specialist_binding"
            index, record = matches[0]
            evidence = _mapping(record.get("evidence", {}), "specialist evidence")
            _facts(record.get("facts", []))
            if not record.get("page_url") or not evidence.get("capture_path"):
                return item, "missing_capture"
            data = read(evidence["capture_path"])
            digest = sha256(data).hexdigest()
            if digest != evidence.get("capture_sha256"):
                return item, "capture_checksum_mismatch"
            item.update(_record=record, _record_index=index,
                        _observed_at=document.get("captured_at"),
                        _source_url=record["page_url"], _source_sha256=digest)
        except OSError:
            return item, "missing_capture"
    return item, None


def _official_extract(item, root, work):
    receipt = {**item["receipt"], "path": str(input_path(root, item["receipt"]["path"]))}
    result = compile_source(item["product"], receipt, work)
    parsed = strict_json(Path(result["parsed_path"]).read_bytes())
    cards = []
    if result.get("artifact"):
        artifact = result["artifact"]
        blob = (Path(result["artifact_root"]) / artifact["path"]).read_bytes()
        if sha256(blob).hexdigest() != artifact["checksum"]:
            raise ValueError("compiled artifact checksum mismatch")
        cards = strict_json(gzip.decompress(blob))
    # The compiler quarantines conflicting raw rows. Preserve each here for review,
    # using its manifest mapping, without feeding duplicates into its dict diff.
    retained = {canonical(r) for r in parsed["rows"]}
    for row in parsed["parsed_rows"]:
        if canonical(row) in retained:
            continue
        source = {**item["product"], "key": item["product_source_key"],
                  "checksum": item["_source_sha256"], "retrieved_at": receipt["captured_at"]}
        entity = next(e for e in build_manifest(source, [row])["entities"]
                      if e["entity_type"] == "card")
        attrs = entity["attributes"]
        attrs.update(source_key=item["product_source_key"],
                     source_variant_label=row.get("source_variant_label"),
                     source_locators=[{k: row[k] for k in ("source_page", "source_line", "raw_text")}])
        cards.append({"entity_type": "card", "canonical_name": entity["canonical_name"],
                      "attributes": attrs, "review_reason": "conflicting_official_row"})
    reasons = sorted(result.get("issue_reasons", {}))
    if result.get("status") == "parse_failed":
        reasons = ["parser_dependency_unavailable" if "No module named" in result.get("error", "")
                   else "document_parse_error"]
    return {"status": "ready" if cards else "blocked", "cards": cards, "facts": [],
            "issue_codes": reasons or ([] if cards else ["no_checklist_rows"])}


def _specialist_extract(item):
    record = item["_record"]
    facts = []
    rows = record.get("facts")
    if not isinstance(rows, list):
        return {"status": "blocked", "cards": [], "facts": [],
                "issue_codes": ["invalid_specialist_facts"]}
    for i, fact in enumerate(rows):
        if not isinstance(fact, dict) or not isinstance(fact.get("field"), str) or "value" not in fact:
            return {"status": "blocked", "cards": [], "facts": [],
                    "issue_codes": ["invalid_specialist_facts"]}
        locator = fact.get("source_locator") or fact.get("locator")
        facts.append({**fact, "locator": locator,
                      "json_pointer": f"/products/{item['_record_index']}/facts/{i}",
                      "authority": record.get("source_authority"), "site": record.get("site"),
                      "capture_kind": record["evidence"].get("capture_kind"),
                      "observed_at": item["_observed_at"], "limits": record.get("limits", []),
                      **({"review_reason": "missing_source_locator"} if not locator else {})})
    return {"status": "ready", "cards": [], "facts": facts, "issue_codes": []}


def extract_sources(inputs: dict[str, Any], cache_root: Path) -> list[dict[str, Any]]:
    """Only deterministic local extraction; cache hits never imply review approval."""
    root, cache = inputs["input_root"].resolve(), cache_root.absolute()
    if cache.resolve().is_relative_to(root) or root.is_relative_to(cache.resolve()):
        raise InputContractError("Cache and input roots must be disjoint")
    if any(p.is_symlink() for p in (cache, *cache.parents)) or (
            cache.exists() and any(p.is_symlink() for p in cache.rglob("*"))):
        raise InputContractError("Cache cannot contain symlinks")
    (cache / "entries").mkdir(parents=True, exist_ok=True)
    (cache / "work").mkdir(exist_ok=True)
    outputs, counts = [], {"cache_hits": 0, "parser_calls": 0, "llm_calls": 0}
    for original in sorted(inputs["items"], key=lambda r: r["input_id"]):
        item, blocked = _prepare(original, inputs)
        base = {"input_id": item["input_id"], "product_source_key": item["product_source_key"],
                "source_url": item.get("_source_url"), "source_sha256": item.get("_source_sha256"),
                "adapter_version": ADAPTER_VERSIONS.get(item["adapter"], "unsupported"),
                "adapter": item["adapter"]}
        payload = None
        if blocked:
            payload = {"status": "blocked", "cards": [], "facts": [], "issue_codes": [blocked]}
        else:
            key = extraction_cache_key(item)
            path = cache / "entries" / f"{key}.json"
            if path.exists():
                try:
                    envelope = strict_json(path.read_bytes())
                    candidate = envelope["payload"]
                    if (envelope["key"] != key
                            or sha256(canonical(candidate).encode()).hexdigest() != envelope["payload_sha256"]
                            or candidate.get("status") != "ready"
                            or not isinstance(candidate.get("cards"), list)
                            or not isinstance(candidate.get("facts"), list)):
                        raise ValueError("invalid cache envelope")
                    payload = candidate
                    counts["cache_hits"] += 1
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    print(f"cache_corrupt:{key}; recomputing", file=sys.stderr)
            if payload is None:
                work = Path(tempfile.mkdtemp(prefix=key + "-", dir=cache / "work"))
                try:
                    if item["adapter"] == "official_document_v1":
                        counts["parser_calls"] += 1
                        payload = _official_extract(item, root, work)
                    else:
                        payload = _specialist_extract(item)
                except (OSError, ValueError, KeyError, TypeError, ImportError):
                    payload = {"status": "blocked", "cards": [], "facts": [],
                               "issue_codes": ["deterministic_extraction_failed"]}
                finally:
                    shutil.rmtree(work)
                envelope = {"key": key, "payload": payload,
                            "payload_sha256": sha256(canonical(payload).encode()).hexdigest()}
                if payload["status"] == "ready":
                    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                        temporary = Path(handle.name)
                        handle.write((canonical(envelope) + "\n").encode())
                    temporary.replace(path)
        outputs.append({**base, **{k: v for k, v in payload.items() if k != "issue_codes"},
                        "issues": [issue(c, input_id=item["input_id"],
                                         product_source_key=item["product_source_key"])
                                   for c in payload["issue_codes"]]})
    print(canonical(counts), file=sys.stderr)
    return outputs
