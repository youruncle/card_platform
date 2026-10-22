import gzip
import hashlib
import json
from pathlib import Path

import pytest

from card_platform.knowledge_card_catalog import ScopedCardCatalog
from card_platform.knowledge_manifest import validate_manifest


CSV_HEADER = 'SPORT,CARD SET,CARD NUMBER,ATHLETE,TEAM,SEQUENCE\n'


def captured_source(tmp_path, rows, *, key="panini-test"):
    raw = tmp_path / "raw" / f"{key}.pdf"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(CSV_HEADER + rows)
    product = {
        "source_key": key,
        "title": "2024-25 Panini Example Basketball",
        "publisher": "Panini",
        "source_url": "https://support.paniniamerica.net/get-product-checklist?program=Example",
        "directory_url": "https://www.paniniamerica.net/checklist.html",
        "season_start": 2024,
        "season_end": 2025,
    }
    receipt = {
        **{k: product[k] for k in ("source_key", "title", "publisher", "source_url")},
        "captured_at": "2026-10-21T01:00:00+00:00",
        "status": "downloaded",
        "http_status": 200,
        "path": str(raw),
        "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        "bytes": raw.stat().st_size,
        "content_type": "text/csv; charset=utf-8",
    }
    return product, receipt


def read_cards(result):
    artifact = result["artifact"]
    blob = (Path(result["artifact_root"]) / artifact["path"]).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == artifact["checksum"]
    return json.loads(gzip.decompress(blob))


def test_rejects_tampered_source_before_emitting_manifest(tmp_path):
    from card_platform.knowledge_text import compile_source

    product, receipt = captured_source(tmp_path, "Basketball,Base,1,Player One,Team One,\n")
    Path(receipt["path"]).write_text(CSV_HEADER + "Basketball,Base,2,Player Two,Team Two,\n")
    with pytest.raises(ValueError, match="checksum"):
        compile_source(product, receipt, tmp_path / "output")
    assert not list((tmp_path / "output").rglob("*.json"))


def test_empty_source_is_preserved_without_complete_or_artifact_claim(tmp_path):
    from card_platform.knowledge_text import compile_source

    product, receipt = captured_source(tmp_path, "")
    result = compile_source(product, receipt, tmp_path / "output")
    assert result["status"] == "no_rows"
    assert result["artifact"] is None
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert validate_manifest(manifest).publishable
    evidence = manifest["entities"][0]["attributes"]["text_evidence"]
    assert evidence["complete_for_captured_source"] is False
    assert evidence["card_fact_count"] == 0
    assert Path(result["parsed_path"]).exists()


def test_unparseable_capture_retains_safe_source_manifest_and_error(tmp_path):
    from card_platform.knowledge_text import compile_source

    product, receipt = captured_source(tmp_path, "")
    raw = Path(receipt["path"])
    raw.write_bytes(b"An official document format that is not supported yet")
    receipt.update(sha256=hashlib.sha256(raw.read_bytes()).hexdigest(), bytes=raw.stat().st_size)
    result = compile_source(product, receipt, tmp_path / "output")
    assert result["status"] == "parse_failed"
    assert result["artifact"] is None
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert validate_manifest(manifest).publishable
    assert manifest["sources"][0]["checksum"] == receipt["sha256"]
    assert [e["entity_type"] for e in manifest["entities"]] == ["product"]
    parsed = json.loads(Path(result["parsed_path"]).read_text())
    assert parsed["issues"][0]["reason"] == "document_parse_error"
    assert "Unsupported official document format" in parsed["issues"][0]["text"]


def test_conflicting_rows_are_quarantined_without_losing_raw_provenance(tmp_path):
    from card_platform.knowledge_text import compile_source

    product, receipt = captured_source(
        tmp_path,
        "Basketball,Base,1,Player One,Team One,\n"
        "Basketball,Base,1,Player One,Other Team,\n"
        "Basketball,Base,2,Player Two,Team Two,\n",
    )
    result = compile_source(product, receipt, tmp_path / "output")
    assert result["status"] == "partial"
    assert result["quarantined_rows"] == 2
    cards = read_cards(result)
    assert [c["attributes"]["card_number"] for c in cards] == ["2"]
    parsed = json.loads(Path(result["parsed_path"]).read_text())
    assert len(parsed["parsed_rows"]) == 3
    assert len(parsed["rows"]) == 1
    assert {i["reason"] for i in parsed["issues"]} == {"conflicting_card_identity"}
    assert parsed["checksum"] == receipt["sha256"]
    assert parsed["pages"][0].startswith(CSV_HEADER)
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["entities"][0]["attributes"]["text_evidence"]["complete_for_captured_source"] is False


def test_bounded_artifact_has_only_explicit_variants_and_runtime_product_scope(tmp_path):
    from card_platform.knowledge_text import compile_source

    product, receipt = captured_source(
        tmp_path,
        "Basketball,Base,1,Player One,Team One,\n"
        "Basketball,Base,2,Player Two,Team Two,\n"
        "Basketball,Base Gold,1,Player One,Team One,10\n",
    )
    result = compile_source(product, receipt, tmp_path / "output")
    assert result["status"] == "compiled"
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert validate_manifest(manifest).publishable
    assert [e["entity_type"] for e in manifest["entities"]] == ["product"]
    attrs = manifest["entities"][0]["attributes"]
    assert attrs["text_evidence"]["section_counts"] == {"Base": 2, "Base Gold": 1}
    assert attrs["text_evidence"]["explicit_variant_counts"] == [
        {"section": "Base", "insert": None, "parallel": None, "serial_total": None, "card_count": 2},
        {"section": "Base Gold", "insert": None, "parallel": "Gold", "serial_total": 10, "card_count": 1},
    ]
    cards = read_cards(result)
    assert len(cards) == 3
    gold = next(c["attributes"] for c in cards if c["attributes"].get("parallel") == "Gold")
    assert gold["card_number"] == "1"
    assert gold["source_filename"] == "panini-test.pdf"
    assert gold["source_key"] == "panini-test"
    assert gold["source_checksum"] == receipt["sha256"]
    assert gold["source_locator"] == "page 1 line 4"
    catalog = ScopedCardCatalog([], artifacts=[result["artifact"]], artifact_root=Path(result["artifact_root"]))
    identity = dict(product_name="Panini Example", brand_name="Panini", season_start=2024,
                    season_end=2025, card_number="1", player_name="Player One")
    assert len(catalog.lookup(identity)) == 2
    assert catalog.lookup(identity | {"product_name": "Panini Different"}) == []
    assert catalog.lookup(identity | {"season_start": 2023, "season_end": 2024}) == []
    again = compile_source(product, receipt, tmp_path / "output")
    assert again["artifact"] == result["artifact"]
    assert Path(again["manifest_path"]).read_bytes() == Path(result["manifest_path"]).read_bytes()
    artifact_path = Path(result["artifact_root"]) / result["artifact"]["path"]
    artifact_path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="immutable artifact"):
        compile_source(product, receipt, tmp_path / "output")


def test_semantic_comparison_excludes_capture_changes_but_records_fact_change(tmp_path):
    from card_platform.knowledge_text import compare_card_facts, compile_source

    product, receipt = captured_source(tmp_path, "Basketball,Base,1,Player One,Team One,\n")
    cards = read_cards(compile_source(product, receipt, tmp_path / "output"))
    previous = json.loads(json.dumps(cards))
    previous[0]["attributes"]["source_checksum"] = "old-capture"
    previous[0]["attributes"]["source_locator"] = "page 2 line 3"
    assert compare_card_facts(cards, previous) == {"new": 0, "changed": 0, "unchanged": 1, "removed": 0}
    previous[0]["attributes"]["team"] = "Other Team"
    assert compare_card_facts(cards, previous) == {"new": 0, "changed": 1, "unchanged": 0, "removed": 0}


def test_multiplayer_order_only_does_not_claim_new_semantic_facts():
    from card_platform.knowledge_text import compare_card_facts

    current = [{"attributes": {"section": "Dual Memorabilia", "card_number": "10",
                              "player": "Brittney Griner/Rhyne Howard",
                              "team": "Team One/Team Two"}}]
    previous = [{"attributes": {"section": "Dual Memorabilia", "card_number": "10",
                               "player": "Rhyne Howard/Brittney Griner",
                               "team": "Team Two/Team One"}}]
    assert compare_card_facts(current, previous) == {"new": 0, "changed": 0, "unchanged": 1, "removed": 0}


def test_inventory_records_missing_capture_and_resumes_other_sources(tmp_path):
    from card_platform.knowledge_text import compile_inventory

    product, receipt = captured_source(tmp_path, "Basketball,Base,1,Player One,Team One,\n")
    text_root = tmp_path / "text"
    (text_root / "receipts").mkdir(parents=True)
    (text_root / "receipts" / "panini-test.json").write_text(json.dumps(receipt))
    other = product | {"source_key": "panini-missing"}
    inventory = {"products": [product, other], "discovery_complete": False}
    report = compile_inventory(inventory, text_root)
    assert report["summary"]["sources_with_manifests"] == 1
    assert report["summary"]["card_fact_count"] == 1
    assert report["summary"]["statuses"] == {"compiled": 1, "missing_capture": 1}
    assert report["discovery_complete"] is False
    _, next_receipt = captured_source(tmp_path, "Basketball,Base,2,Player Two,Team Two,\n", key="panini-missing")
    (text_root / "receipts" / "panini-missing.json").write_text(json.dumps(next_receipt))
    resumed = compile_inventory(inventory, text_root)
    assert resumed["summary"]["sources_with_manifests"] == 2
    assert resumed["summary"]["statuses"] == {"compiled": 2}
    assert (text_root / "compile-report.json").exists()


def test_real_uconn_excerpt_retains_alpha_numbers_and_explicit_shortprint_rows():
    from card_platform.knowledge_text import parse_uconn_pages

    # Verbatim logical extraction from official PDF SHA c98ddf7a...56d4e.
    rows, issues = parse_uconn_pages([
        "COLLEGE CONTENDERS\n 1 Paige Bueckers\n"
        "LEGENDARY CONTENDERS\n\t36 Breanna Stewart SP\n"
        "CAMPUS LEGENDS\n\tDH Dan Hurley  SP\n\tGA Geno Auriemma  SP\n"
        "2025 PANINI UNIVERSITY OF CONNECTICUT® CHECKLIST",
        "PRIVATE SIGNINGS\n\tAK Alex Karaban\n\tAK Alex Karaban  SP\n"
        "ON CAMPUS\n\tOC-1 Paige Bueckers SSP\nPINNACLE MASCOT\n"
        "2025 PANINI UNIVERSITY OF CONNECTICUT® CHECKLIST",
    ])
    assert len(rows) == 7
    assert rows[1]["player"] == "Breanna Stewart"
    assert rows[1]["source_variant_label"] == "SP"
    assert rows[2]["card_number"] == "DH"
    assert rows[2]["player"] == "Dan Hurley"
    assert rows[3]["card_number"] == "GA"
    assert rows[4].get("source_variant_label") is None
    assert rows[5]["source_variant_label"] == "SP"
    assert rows[6]["source_variant_label"] == "SSP"
    assert rows[5]["source_page"] == 2
    assert rows[5]["source_line"] == 3
    assert rows[5]["team"] is None
    assert rows[5].get("serial_total") is None
    assert rows[5].get("parallel") is None
    assert issues == [{"page": 2, "line": 6, "text": "PINNACLE MASCOT",
                       "reason": "section_without_card_rows"}]


def test_real_uconn_pdf_recovery_preserves_169_rows_and_variant_identity(tmp_path):
    from card_platform.knowledge_text import compare_card_facts, compile_source

    raw = Path(__file__).resolve().parents[3] / "data/knowledge-three-seasons/text/raw/panini-api-2025-1507-official-pdf.pdf"
    if not raw.exists():
        pytest.skip("Local official UConn capture is unavailable")
    product = {
        "source_key": "panini-api-2025-1507",
        "title": "2025 Panini UCONN Box Set Basketball",
        "publisher": "Panini", "season_start": 2025, "season_end": 2025,
        "source_url": "https://blog.paniniamerica.net/wp-content/uploads/2025/03/UConn-checklist1-1.pdf",
    }
    receipt = {
        **{k: product[k] for k in ("source_key", "title", "publisher", "source_url")},
        "captured_at": "2026-10-21T02:34:14+00:00", "status": "downloaded",
        "http_status": 200, "path": str(raw), "bytes": 605969,
        "sha256": "c98ddf7a3c08e94fbcd2defc46f6f0e195228bb6e2b367d5d642b9f5b5856d4e",
    }
    result = compile_source(product, receipt, tmp_path / "output")
    assert result["status"] == "partial"
    cards = read_cards(result)
    assert len(cards) == 169
    assert len({c["id"] for c in cards}) == 169
    assert compare_card_facts(cards, []) == {"new": 169, "changed": 0, "unchanged": 0, "removed": 0}
    alex = [c["attributes"] for c in cards if c["attributes"]["section"] == "PRIVATE SIGNINGS"
            and c["attributes"]["card_number"] == "AK"]
    assert len(alex) == 2
    assert {a.get("source_variant_label") for a in alex} == {None, "SP"}
    assert all(len(a["source_locators"]) == 1 for a in alex)
    assert all(c["attributes"]["season_end"] == 2025 for c in cards)
    assert all(c["attributes"].get("serial_total") is None for c in cards)
    assert result["quarantined_rows"] == 1

