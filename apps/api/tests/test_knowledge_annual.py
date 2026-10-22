import gzip
from hashlib import sha256
import json


def _source(key, title, publisher="Topps", **fields):
    return dict(key=key, title=title, publisher=publisher,
                source_url="https://www.topps.com/" + key + ".pdf", **fields)


def test_inventory_preserves_release_date_and_ownership_evidence(tmp_path):
    from card_platform.knowledge_annual import build_annual_inventory

    source = _source("a", "2025-26 Topps Chrome Basketball", release_date="2026-01-01",
                     date_basis="official directory release_date", ownership_evidence="Topps imprint",
                     document_format="PDF", retrieval_format="GET", date_scope="product_release")
    product = build_annual_inventory({"products": [source]}, {}, tmp_path)["products"][0]
    for key in ("date_basis", "date_scope", "ownership_evidence", "document_format", "retrieval_format"):
        assert product[key] == source[key]


def test_inventory_preserves_source_proven_canonical_title_and_raw_directory_title(tmp_path):
    from card_platform.knowledge_annual import build_annual_inventory

    raw = "2025 Panini UCONN Box Set Basketball"
    canonical = "2025 Panini University of Connecticut Basketball Box Set"
    result = build_annual_inventory({"products": [_source("a", raw, "Panini", canonical_title=canonical)]}, {}, tmp_path)
    assert result["products"][0]["canonical_title"] == canonical
    assert result["products"][0]["directory_title"] == raw


def test_inventory_uses_release_season_not_archive_year_or_owned_cards(tmp_path):
    from card_platform.knowledge_annual import build_annual_inventory

    catalog = {"products": [
        _source("a", "2024-25 Topps Chrome Basketball", archive_year=2025),
        _source("b", "2022-23 Topps Chrome Basketball"),
        _source("c", "2025-26 Panini Prizm Basketball", "Panini"),
        _source("d", "2026-27 Topps Basketball"),
    ]}
    result = build_annual_inventory(catalog, {}, tmp_path)
    assert [(p["source_key"], p["season_start"]) for p in result["products"]] == [
        ("c", 2025), ("a", 2024)]
    assert result["summary"]["products"] == 2
    assert result["summary"]["image_pairs"] == 0


def test_verified_cached_artifact_counts_text_separately_from_images(tmp_path):
    from card_platform.knowledge_annual import build_annual_inventory

    blob = gzip.compress(json.dumps([{"id": "a"}, {"id": "b"}]).encode())
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "cards.json.gz").write_bytes(blob)
    catalog = {"products": [_source("a", "2025-26 Topps Chrome Basketball",
        status="published", artifact=dict(path="cards.json.gz", checksum=sha256(blob).hexdigest(), card_count=2))]}
    result = build_annual_inventory(catalog, {}, tmp_path)
    product = result["products"][0]
    assert product["text_status"] == "cached_verified"
    assert product["card_fact_count"] == 2
    assert product["image_status"] == "pending"
    assert result["summary"]["text_fact_records"] == 2
    assert result["summary"]["full_image_percent"] is None


def test_corrupt_artifact_does_not_count_as_verified_text(tmp_path):
    from card_platform.knowledge_annual import build_annual_inventory

    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "bad.gz").write_bytes(b"corrupt")
    catalog = {"products": [_source("a", "2025-26 Topps Chrome Basketball",
        artifact=dict(path="bad.gz", checksum="0" * 64, card_count=99))]}
    result = build_annual_inventory(catalog, {}, tmp_path)
    assert result["products"][0]["text_status"] == "invalid_artifact"
    assert result["summary"]["text_fact_records"] == 0


def test_live_directory_duplicates_merge_without_erasing_cached_fact_evidence(tmp_path):
    from card_platform.knowledge_annual import merge_discovery

    previous = _source("a", "2025-26 Topps Chrome Basketball", checksum="abc", row_count=299, status="published")
    live = _source("a", "2025-26 Topps Chrome Basketball", status="pending")
    result = merge_discovery({"products": [previous]}, [live])
    assert len(result["products"]) == 1
    assert result["products"][0]["checksum"] == "abc"
    assert result["products"][0]["row_count"] == 299


def test_unknown_season_and_excluded_sources_are_reported_not_imported(tmp_path):
    from card_platform.knowledge_annual import build_annual_inventory

    catalog = {"products": [_source("a", "Topps Basketball"),
        _source("b", "2024-25 Topps Chrome Basketball", status="excluded")]}
    result = build_annual_inventory(catalog, {}, tmp_path)
    assert result["products"] == []
    assert result["summary"]["unknown_season"] == 1
    assert result["summary"]["excluded"] == 1


def test_manifest_card_rows_are_available_without_pretending_raw_source_is_fresh(tmp_path):
    from card_platform.knowledge_annual import build_annual_inventory

    manifest = {"entities": [{"entity_type": "card", "attributes": {"card_number": "1"}},
                             {"entity_type": "product", "attributes": {"product": "Topps Chrome"}}]}
    result = build_annual_inventory({"products": [_source("a", "2025-26 Topps Chrome Basketball")]},
                                    {"a": manifest}, tmp_path)
    assert result["products"][0]["card_fact_count"] == 1
    assert result["products"][0]["text_status"] == "parsed_cached"
    assert result["products"][0]["source_freshness"] == "not_refetched"


def test_blocked_directory_capture_keeps_known_inventory_and_retains_http_evidence(tmp_path):
    import httpx
    from card_platform.knowledge_annual import capture_topps_directory

    transport = httpx.MockTransport(lambda request: httpx.Response(403, text="Access denied"))
    catalog = {"products": [_source("a", "2025-26 Topps Chrome Basketball")]}
    with httpx.Client(transport=transport) as client:
        result = capture_topps_directory(catalog, tmp_path, client)
    assert result["catalog"] == catalog
    assert result["discovery_status"]["status"] == "blocked"
    assert result["capture"]["http_status"] == 403
    assert (tmp_path / result["capture"]["filename"]).read_text() == "Access denied"


def test_official_directory_typo_retains_raw_label_but_provides_canonical_product_title(tmp_path):
    from card_platform.knowledge_annual import build_annual_inventory

    result = build_annual_inventory({"products": [_source("hoops", "2025-26 Topps Hopps Basketball")]}, {}, tmp_path)
    product = result["products"][0]
    assert product["directory_title"] == "2025-26 Topps Hopps Basketball"
    assert product["canonical_title"] == "2025-26 Topps Hoops Basketball"
    assert product["product"] == "Topps Hoops"
