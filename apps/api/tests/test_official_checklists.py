import pytest
from card_platform.official_checklists import (
    parse_pages,
    progress_summary,
    build_manifest,
    official_url,
    window_status,
)


def test_cross_page_section_and_card_columns():
    rows, issues = parse_pages(
        [
            "ROSES\nR-5    Tracy McGrady      Houston Rockets\n",
            "R-6    LaMelo Ball      Charlotte Hornets\nARISTOCRATS\nAR-1    Stephen Curry      Golden State Warriors",
        ]
    )
    assert len(rows) == 3
    assert rows[1]["section"] == "ROSES" and rows[1]["source_page"] == 2
    assert rows[0]["player"] == "Tracy McGrady"
    assert rows[2]["insert"] == "Aristocrats"
    assert not issues


def test_unreadable_card_row_is_recorded_not_misattributed():
    rows, issues = parse_pages(
        ["BASE\n1    Player Name     Chicago Bulls\nR-5 no column boundaries\n"]
    )
    assert len(rows) == 1 and issues
    assert rows[0]["insert"] is None


def test_failed_products_never_count_as_published():
    products = [{"status": "published"}, {"status": "failed"}, {"status": "parsed"}]
    p = progress_summary(products, discovery_complete=True)
    assert p["success_percent"] == 33.3
    assert p["processed_percent"] == 66.7
    assert progress_summary(products, discovery_complete=False)["success_percent"] is None
    assert progress_summary([], discovery_complete=True)["success_percent"] is None


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/x.pdf",
        "https://topps.com.evil.test/x.pdf",
        "file:///etc/passwd",
        "http://www.topps.com/x.pdf",
        "https://cdn.shopify.com/s/files/1/999/evil.pdf",
    ],
)
def test_only_official_document_origins(url):
    assert not official_url(url)


def test_unknown_release_date_is_not_season_date():
    assert window_status(None, "2025-10-04", "2026-10-04") == "unknown"
    assert window_status("2025-10-04", "2025-10-04", "2026-10-04") == "in_window"
    assert window_status("2025-10-03", "2025-10-04", "2026-10-04") == "outside"


def test_manifest_has_row_evidence_and_no_invented_parallel():
    product = {
        "title": "2025-26 Topps Signature Class Basketball",
        "publisher": "Topps",
        "source_url": "https://cdn.shopify.com/s/files/1/0662/9749/5709/files/a.pdf",
        "checksum": "abc",
        "key": "signature",
    }
    rows, _ = parse_pages(["ROSES\nR-5    Tracy McGrady      Houston Rockets"])
    manifest = build_manifest(product, rows)
    card = next(x for x in manifest["entities"] if x["entity_type"] == "card")
    assert card["attributes"]["insert"] == "Roses"
    assert card["attributes"]["card_number"] == "R-5"
    assert card["attributes"]["source_locator"] == "page 1 line 2"
    assert card["review_status"] == "approved"
    assert "parallel" not in card["attributes"]
    from card_platform.knowledge_manifest import validate_manifest

    assert validate_manifest(manifest).publishable


def test_single_space_card_separator_and_letter_card_numbers():
    rows, issues = parse_pages(
        [
            "BASE CARDS\n1 Cooper Flagg             Dallas Mavericks",
            "AUTOGRAPHS\nAU-TM Tracy McGrady          Houston Rockets",
        ]
    )
    assert len(rows) == 2 and not issues
    assert rows[1]["card_number"] == "AU-TM"


def test_manifest_is_stable_for_cached_source():
    product = {
        "title": "2025-26 Topps Basketball",
        "publisher": "Topps",
        "source_url": "https://www.topps.com/checklist.pdf",
        "checksum": "abc",
        "key": "test",
    }
    assert build_manifest(product, []) == build_manifest(product, [])


def test_xlsx_uses_same_source_locator_contract(tmp_path):
    from openpyxl import Workbook
    from card_platform.official_checklists import parse_document

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Roses"
    sheet.append(["R-5", "Tracy McGrady", "Houston Rockets"])
    path = tmp_path / "official.xlsx"
    workbook.save(path)
    rows, issues, pages = parse_document(path)
    assert not issues and rows[0]["insert"] == "Roses"
    assert rows[0]["source_page"] == 1 and rows[0]["source_line"] == 2


def test_numbered_section_does_not_leak_prior_insert():
    rows, issues = parse_pages(
        [
            "FUTURE STARS\nF-1    Cooper Flagg    Dallas Mavericks\n3 AND D\nD-1    Stephen Curry    Golden State Warriors"
        ]
    )
    assert not issues
    assert rows[1]["insert"] == "3 And D"


def test_progress_endpoint_returns_fixed_status_file(tmp_path, monkeypatch):
    import asyncio
    import json
    from card_platform import official_checklists
    from card_platform.knowledge_api import official_checklist_progress

    path = tmp_path / "status.json"
    monkeypatch.setattr(official_checklists, "STATUS_PATH", path)
    assert asyncio.run(official_checklist_progress())["engineering_percent"] == 0
    path.write_text(json.dumps({"engineering_percent": 65, "products": []}))
    assert asyncio.run(official_checklist_progress())["engineering_percent"] == 65


def test_discovery_accepts_only_official_basketball_documents():
    from card_platform.official_checklists import discover_topps_links

    html = """<a href="https://cdn.shopify.com/s/files/1/0662/9749/5709/files/a.pdf?v=1">2025-26 Topps Basketball</a>
    <a href="https://evil.test/b.pdf">Basketball</a><a href="/a.pdf">Baseball</a>"""
    products = discover_topps_links(html, "https://www.topps.com/pages/checklists")
    assert len(products) == 1 and products[0]["release_date"] is None


def test_wrapped_card_number_does_not_become_insert_name():
    rows, issues = parse_pages(
        [
            "1973 TOPPS AUTOGRAPHS\n73TA-\nPMI    Patty Mills    Utah\n73TA-SC    Stephen Curry    San Francisco"
        ]
    )
    assert not issues and len(rows) == 2
    assert rows[0]["card_number"] == "73TA-PMI"
    assert rows[1]["insert"] == "1973 Topps Autographs"


def test_sell_sheet_rows_are_not_checklist_identity_facts():
    rows, issues = parse_pages(
        ["BASE\n1    Player Name    Team Name\nSELL SHEET\n1001    Joel Embiid    Philadelphia"]
    )
    assert len(rows) == 1
    assert issues[0]["reason"] == "unresolved_columns_or_section"


def test_glued_card_number_and_player_name_are_quarantined():
    rows, issues = parse_pages(
        [
            "AUTOGRAPHS\nNTN-10Anthony Davis    Los Angeles Lakers\nCSIGN-BBARKERBob Barker    TV Personality\nBV-Lm    LaMelo Ball    Charlotte Hornets"
        ]
    )
    assert [row["card_number"] for row in rows] == ["BV-Lm"]
    assert len(issues) == 2 and all(i["reason"] == "glued_card_number_and_player" for i in issues)


def test_panini_csv_preserves_variants_and_rejects_cross_product_sections(tmp_path):
    from card_platform.official_checklists import parse_document

    path = tmp_path / 'official.csv'
    path.write_text('SPORT,YEAR,BRAND,PROGRAM,CARD SET,ATHLETE,TEAM,POSITION,CARD NUMBER,SEQUENCE\n'
                    'Basketball,2025,Panini,Court Kings (25-26),Base,Player Name,Team,G,1,\n'
                    'Basketball,2025,Panini,Court Kings (25-26),Base Ruby,Player Name,Team,G,1,49\n'
                    'Basketball,2025,Panini,Court Kings (25-26),Works In Progress,Player Name,Team,G,2,\n'
                    'Basketball,2025,Panini,Court Kings (25-26),Works In Progress Violet,Player Name,Team,G,2,49\n'
                    'Basketball,2025,Panini,Court Kings (25-26),2025 Panini Select Basketball - Instant,Other Player,Team,G,3,\n')
    rows, issues, _ = parse_document(path)
    assert len(rows) == 4
    assert rows[1]['parallel'] == 'Ruby' and rows[1]['serial_total'] == 49
    assert rows[1]['insert'] is None
    assert rows[3]['insert'] == 'Works In Progress'
    assert rows[3]['parallel'] == 'Violet'
    assert issues[0]['reason'] == 'cross_product_section'


def test_panini_base_variants_never_become_insert_names():
    from card_platform.official_checklists import parse_panini_csv

    text = ('SPORT,YEAR,BRAND,PROGRAM,CARD SET,ATHLETE,TEAM,POSITION,CARD NUMBER,SEQUENCE\n'
            'Basketball,2025,Panini,Signature Series,Base Plaid,Kevin Garnett,Team,F,94,\n'
            'Basketball,2025,Panini,Signature Series,Base Cracked Ice,Dirk Nowitzki,Team,F,97,\n')
    rows, issues, _ = parse_panini_csv(text)
    assert not issues
    assert all(row['insert'] is None for row in rows)
