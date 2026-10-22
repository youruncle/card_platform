"""Source-driven annual inventory; text, image and vector coverage stay distinct."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path

from card_platform.official_checklists import discover_topps_links, product_fields


def capture_topps_directory(catalog: dict, folder: Path, client) -> dict:
    """A failed discovery must not erase verified cached source facts."""
    import httpx

    url = "https://www.topps.com/pages/checklists"
    captured_at = datetime.now(timezone.utc).isoformat()
    try:
        response = client.get(url, timeout=60, follow_redirects=True)
        digest = sha256(response.content).hexdigest()
        filename = digest + ".html"
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(response.content)
        capture = {"url": url, "filename": filename, "sha256": digest,
                   "captured_at": captured_at, "http_status": response.status_code}
        response.raise_for_status()
        discoveries = discover_topps_links(response.text, url)
        if not discoveries:
            return {"catalog": catalog, "capture": capture,
                    "discovery_status": {"status": "empty", "links": 0}}
        return {"catalog": merge_discovery(catalog, discoveries), "capture": capture,
                "discovery_status": {"status": "fetched", "links": len(discoveries)}}
    except httpx.HTTPError as error:
        return {"catalog": catalog, "capture": locals().get("capture", {"url": url, "captured_at": captured_at}),
                "discovery_status": {"status": "blocked", "error": str(error)}}


def merge_discovery(catalog: dict, discovered: list[dict]) -> dict:
    result = deepcopy(catalog)
    products = {p["key"]: p for p in result.get("products", [])}
    for candidate in discovered:
        key = candidate["key"]
        if key not in products:
            products[key] = deepcopy(candidate)
        else:
            previous = products[key]
            for name, value in candidate.items():
                if name not in previous or previous[name] in (None, "", []):
                    previous[name] = deepcopy(value)
            previous["live_directory_seen"] = True
    result["products"] = list(products.values())
    return result


def _text_state(source: dict, manifest: dict, root: Path) -> tuple[str, int, str | None]:
    reference = source.get("artifact")
    if not reference:
        for entity in manifest.get("entities", []):
            if entity.get("entity_type") == "product":
                reference = entity.get("attributes", {}).get("official_artifact")
                if reference:
                    break
    if reference:
        try:
            base = (root / "artifacts").resolve()
            target = (base / reference["path"]).resolve()
            if not target.is_relative_to(base):
                raise ValueError("artifact_path_outside_root")
            raw = target.read_bytes()
            if sha256(raw).hexdigest() != reference["checksum"]:
                raise ValueError("artifact_checksum_mismatch")
            rows = json.loads(gzip.decompress(raw))
            if not isinstance(rows, list) or len(rows) != reference["card_count"]:
                raise ValueError("artifact_card_count_mismatch")
            return "cached_verified", len(rows), None
        except (OSError, ValueError, KeyError, TypeError) as error:
            return "invalid_artifact", 0, str(error)
    cards = [e for e in manifest.get("entities", []) if e.get("entity_type") == "card"]
    if cards:
        return "parsed_cached", len(cards), None
    return "missing_source", 0, source.get("error")


def build_annual_inventory(catalog: dict, manifests: dict[str, dict], root: Path,
                           season_starts=(2023, 2024, 2025)) -> dict:
    products = []
    unknown = []
    excluded = 0
    for source in catalog.get("products", []):
        if source.get("status") == "excluded":
            excluded += 1
            continue
        try:
            fields = product_fields(source["title"])
        except (ValueError, KeyError, TypeError):
            unknown.append({"source_key": source.get("key"), "title": source.get("title")})
            continue
        if fields.get("season_start") is None:
            unknown.append({"source_key": source.get("key"), "title": source.get("title")})
            continue
        if fields["season_start"] not in season_starts:
            continue
        publisher = source.get("publisher")
        if publisher not in {"Topps", "Panini"}:
            continue
        manifest = manifests.get(source["key"], {})
        text_status, count, error = _text_state(source, manifest, Path(root))
        product = {
            "source_key": source["key"], "title": source["title"], "publisher": publisher,
            "directory_title": source["title"],
            "canonical_title": source.get("canonical_title") or source["title"].replace(" Hopps ", " Hoops "),
            **fields, "source_url": source["source_url"],
            "directory_url": source.get("directory_url"),
            "source_pages": list(source.get("source_pages", [])),
            "release_date": source.get("release_date"),
            **{key: source.get(key) for key in (
                "date_basis", "date_scope", "ownership_evidence", "document_format", "retrieval_format")},
            "source_checksum": source.get("checksum"),
            "source_freshness": "refetched" if source.get("refetched_at") else "not_refetched",
            "refetched_at": source.get("refetched_at"),
            "text_status": text_status, "card_fact_count": count,
            "quarantined_rows": source.get("issue_count", 0),
            "image_status": "pending", "vector_status": "pending", "image_pairs": 0,
            "previous_import_status": source.get("status", "pending"),
        }
        if error:
            product["text_error"] = error
        products.append(product)
    products.sort(key=lambda p: (-p["season_start"], p["publisher"], p["title"], p["source_key"]))
    by_year = {}
    for year in sorted(season_starts, reverse=True):
        current = [p for p in products if p["season_start"] == year]
        by_year[str(year)] = {"products": len(current),
                             "publishers": dict(Counter(p["publisher"] for p in current)),
                             "text_fact_records": sum(p["card_fact_count"] for p in current)}
    return {
        "contract": "annual-source-knowledge-v1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "season_starts": list(season_starts), "selection_basis": "official_product_season",
        "discovery_complete": False, "products": products, "unknown_season": unknown,
        "summary": {"products": len(products), "text_fact_records": sum(p["card_fact_count"] for p in products),
                    "text_statuses": dict(Counter(p["text_status"] for p in products)),
                    "image_pairs": 0, "full_image_percent": None, "vector_percent": None,
                    "unknown_season": len(unknown), "excluded": excluded, "by_season": by_year},
        "limits": ["Source discovery is not a proof of exhaustive annual releases.",
                   "Expanded checklist records include variants, not distinct physical cards.",
                   "Cached text counts do not establish image coverage or fresh source access."],
    }
