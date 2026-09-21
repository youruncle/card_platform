from card_platform.valuation_worker import AutomatedValuationWorker


def test_confirmed_identity_wins_without_mutating_source_row():
    row = {
        "player_name": "Projection",
        "card_number": "1",
        "confirmed_identity": {
            "player_name": "Confirmed",
            "card_number": "2",
            "product_name": "Example",
            "season_start": 2025,
        },
        "scanner_recognition": None,
    }
    assert hasattr(AutomatedValuationWorker, "resolve_identity_row"), (
        "shared pure identity resolver missing"
    )
    worker = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
    worker.knowledge_runtime = None
    result = worker.resolve_identity_row(row)
    assert result["card_number"] == "2" and result["player_name"] == "Confirmed"
    assert row["card_number"] == "1" and row["confirmed_identity"]["card_number"] == "2"


def test_print_run_fraction_is_not_a_catalog_number():
    from card_platform import image_service as module
    from card_platform.schemas import CardRecognitionResult

    result = CardRecognitionResult(
        confidence=".95",
        card_number="35/50",
        serial_number=35,
        serial_total=50,
        field_evidence={"card_number": ["正面左下角清晰显示35/50"]},
    )
    cleaned = module.sanitize_identity_field_roles(result)
    assert cleaned.card_number is None
    assert (cleaned.serial_number, cleaned.serial_total) == (35, 50)
    assert cleaned.recognition_status == "research_required"


def test_roster_entry_cannot_be_a_card_number():
    from card_platform import image_service as module
    from card_platform.schemas import CardRecognitionResult

    result = CardRecognitionResult(
        confidence=".95",
        card_number="203-211",
        field_evidence={"card_number": ["卡背名单左侧编号：203–211"]},
    )
    assert module.sanitize_identity_field_roles(result).card_number is None


def test_explicit_gr_identifier_survives_field_role_guard():
    from card_platform import image_service as module
    from card_platform.schemas import CardRecognitionResult

    result = CardRecognitionResult(
        confidence=".95",
        card_number="GR-5",
        field_evidence={"card_number": ["背面右上独立卡号 GR-5，不是名单编号"]},
    )
    assert module.sanitize_identity_field_roles(result).card_number == "GR-5"


def test_statistics_or_inferred_copyright_season_is_not_release_evidence():
    from card_platform import image_service as module
    from card_platform.schemas import CardRecognitionResult

    result = CardRecognitionResult(
        confidence=".95",
        season_start=2021,
        season_end=2022,
        copyright_year=2026,
        field_evidence={
            "season_start": ["卡背版权 ©2026，结合产品为2021-22 NBA Hoops推断发行年"],
            "season_end": ["卡背版权 ©2026，结合产品为2021-22 NBA Hoops推断发行年"],
        },
    )
    cleaned = module.sanitize_identity_field_roles(result)
    assert cleaned.season_start is None and cleaned.season_end is None
    assert cleaned.copyright_year == 2026


def test_printed_release_year_is_retained():
    from card_platform import image_service as module
    from card_platform.schemas import CardRecognitionResult

    result = CardRecognitionResult(
        confidence=".95",
        season_start=2023,
        season_end=2024,
        copyright_year=2024,
        field_evidence={
            "season_start": ["卡背产品行明确印2023-24 PANINI COURT KINGS BASKETBALL"],
            "season_end": ["卡背产品行明确印2023-24 PANINI COURT KINGS BASKETBALL"],
        },
    )
    assert module.sanitize_identity_field_roles(result).season_start == 2023


def test_explicitly_negated_wrong_roles_do_not_erase_correct_number_or_season():
    from card_platform.image_service import sanitize_identity_field_roles
    from card_platform.schemas import CardRecognitionResult

    for evidence in [
        "背面右上独立卡号 123，不是球衣号或名单编号",
        "Official checklist confirms card #123",
    ]:
        result = CardRecognitionResult(
            confidence=".95",
            card_number="123",
            season_start=2023,
            field_evidence={
                "card_number": [evidence],
                "season_start": ["卡背产品行明确印2023-24，并非统计年份或推断"],
            },
        )
        cleaned = sanitize_identity_field_roles(result)
        assert cleaned.card_number == "123" and cleaned.season_start == 2023


def test_biography_year_and_draft_year_are_not_release_seasons():
    from card_platform.image_service import sanitize_identity_field_roles
    from card_platform.schemas import CardRecognitionResult

    result = CardRecognitionResult(
        confidence=".98",
        season_start=2024,
        season_end=2025,
        copyright_year=2025,
        field_evidence={
            "season_start": ["背面: in 2024/25 as he posterized", "背面: DRAFTED: 2025"],
            "season_end": ["背面: in 2024/25"],
            "copyright_year": ["背面: © TOPPS"],
        },
    )
    cleaned = sanitize_identity_field_roles(result)
    assert cleaned.season_start is None and cleaned.season_end is None
    assert cleaned.copyright_year is None
    assert cleaned.recognition_status == "research_required"


def test_explicit_release_line_survives_additional_biography_evidence():
    from card_platform.image_service import sanitize_identity_field_roles
    from card_platform.schemas import CardRecognitionResult

    result = CardRecognitionResult(
        confidence=".96",
        season_start=2025,
        season_end=2026,
        copyright_year=2026,
        field_evidence={
            "season_start": ["发行产品行: 2025-26 PANINI NBA HOOPS", "DRAFTED: 2025"],
            "season_end": ["发行产品行: 2025-26 PANINI NBA HOOPS"],
            "copyright_year": ["卡背 © 2026 Panini America"],
        },
    )
    cleaned = sanitize_identity_field_roles(result)
    assert (cleaned.season_start, cleaned.season_end, cleaned.copyright_year) == (2025, 2026, 2026)


def test_release_evidence_must_support_the_returned_year_not_any_year():
    from card_platform.image_service import sanitize_identity_field_roles
    from card_platform.schemas import CardRecognitionResult

    for claims in [
        ["发行产品行: 2025-26 PANINI NBA HOOPS", "DRAFTED: 2024"],
        ["No release year printed; DRAFTED: 2024"],
    ]:
        result = CardRecognitionResult(
            confidence=".95", season_start=2024, field_evidence={"season_start": claims}
        )
        assert sanitize_identity_field_roles(result).season_start is None


def test_valid_release_evidence_survives_separate_biography_claim():
    from card_platform.image_service import sanitize_identity_field_roles
    from card_platform.schemas import CardRecognitionResult

    result = CardRecognitionResult(
        confidence=".95",
        season_start=2025,
        field_evidence={
            "season_start": ["发行产品行: 2025-26 PANINI NBA HOOPS", "球员生涯 in 2024/25 的表现"]
        },
    )
    assert sanitize_identity_field_roles(result).season_start == 2025


def test_biography_season_cannot_return_via_local_ocr_anchor():
    from card_platform.local_ocr import extract_anchors

    assert "season_start" not in extract_anchors(["in 2024/25 basketball season"])
    assert extract_anchors(["2025-26 PANINI NBA HOOPS"])["season_start"] == 2025


def test_unresolved_copyright_evidence_issue_survives_finalization():
    from card_platform.image_service import sanitize_identity_field_roles
    from card_platform.vision_routing import finalize_recognition_status
    from card_platform.schemas import CardRecognitionResult

    result = CardRecognitionResult(
        confidence=".98",
        player_name="Example",
        season_start=2025,
        season_end=2026,
        brand_name="Topps",
        product_name="Chrome",
        card_number="1",
        copyright_year=2025,
        field_evidence={"copyright_year": ["© TOPPS"]},
    )
    cleaned = finalize_recognition_status(sanitize_identity_field_roles(result))
    assert cleaned.copyright_year is None
    assert cleaned.recognition_status == "research_required"
    assert "copyright_year_not_in_quoted_evidence" in cleaned.recognition_quality_issues


def test_inferred_release_statement_is_not_direct_product_line():
    from card_platform.image_service import sanitize_identity_field_roles
    from card_platform.schemas import CardRecognitionResult

    for text in [
        "inferred release year 2024 from draft date",
        "推断发行产品为2024-25 PANINI NBA HOOPS",
    ]:
        result = CardRecognitionResult(
            confidence=".95", season_start=2024, field_evidence={"season_start": [text]}
        )
        assert sanitize_identity_field_roles(result).season_start is None
