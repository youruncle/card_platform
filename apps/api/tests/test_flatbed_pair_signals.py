import importlib

import pytest


def extract(text):
    return importlib.import_module("card_platform.flatbed_pair_signals").extract_pair_signals(text)


def test_commemorative_date_and_biography_do_not_become_release_year_or_serial():
    signals = extract("MARCH 2, 2024\n3/2/24\nLEBRON JAMES\nINSTANT\nBorn 1984\nplayed in 2021-22")
    assert signals["product_name"] == "Instant"
    assert signals["player_name"] == "LeBron James"
    assert "year" not in signals
    assert "season_start" not in signals
    assert "serial_number" not in signals


@pytest.mark.parametrize(
    "line,product,start,end",
    [
        ("2023-24 PANINI - INSTANT BASKETBALL", "Instant", 2023, 2024),
        ("2024–25 PANINI – DONRUSS OPTIC BASKETBALL", "Donruss Optic", 2024, 2025),
        ("1999-2000 UPPER DECK SP AUTHENTIC BASKETBALL", "SP Authentic", 1999, 2000),
    ],
)
def test_explicit_release_line_preserves_product_and_season(line, product, start, end):
    signals = extract("Memorable 2014-15 season\n" + line + "\n© 2025 Panini America")
    assert signals["product_name"] == product
    assert (signals["season_start"], signals["season_end"]) == (start, end)
    assert "year" not in signals


def test_subset_prizm_logo_does_not_override_select_product_footer():
    signals = extract("No. 54\nCONCOURSE\nPRIZM\nLEBRON JAMES\n2024-25 PANINI - SELECT BASKETBALL")
    assert signals["product_name"] == "Select"
    assert signals["insert_name"] == "Concourse"
    assert signals["card_number"] == "54"


def test_first_stats_year_does_not_override_footer_season():
    signals = extract(
        "Returned in 2014-15\nNo. 5\nTROPHY CLUB\nLEBRON JAMES\n2019-20 PANINI - STATUS BASKETBALL"
    )
    assert signals["product_name"] == "Status"
    assert signals["insert_name"] == "Trophy Club"
    assert signals["season_start"] == 2019


def test_card_number_and_serial_are_not_date_or_manufacturing_code():
    signals = extract("No. 154\n08/20\n3/2/24\nWWW.TOPPS.COM CODE#CMP127529")
    assert signals["card_number"] == "154"
    assert (signals["serial_number"], signals["serial_total"]) == (8, 20)


def test_unknown_text_does_not_invent_identity_from_generic_teams_or_name_fragments():
    signals = extract("LOS ANGELES LAKERS\nJAMES\nMARCH 2 2024\nCopyright 2025\nNO MATTER WHAT")
    assert not any(
        key in signals for key in ("player_name", "product_name", "season_start", "card_number")
    )
    assert signals["ocr_text"].startswith("LOS ANGELES")
    assert "james" in signals["ocr_tokens"]


def test_conflicting_product_footer_lines_do_not_choose_one_arbitrarily():
    signals = extract("2023-24 PANINI - INSTANT BASKETBALL\n2024-25 PANINI - SELECT BASKETBALL")
    assert "product_name" not in signals
    assert "season_start" not in signals
    assert signals["identity_signal_issues"]


def test_spaced_date_cannot_become_serial_and_stats_ratios_are_not_stamps():
    signals = extract("3 / 2 / 24\nSeason 2023/24\nFG 5/12")
    assert "serial_number" not in signals


def test_callers_cannot_mutate_another_regions_cached_observations():
    first = extract("No. 5\nLEBRON JAMES\nINSTANT")
    first["ocr_tokens"].append("polluted")
    first["identity_signal_evidence"]["player_name"] = "another card"
    second = extract("No. 5\nLEBRON JAMES\nINSTANT")
    assert "polluted" not in second["ocr_tokens"]
    assert second["identity_signal_evidence"]["player_name"] == "LeBron James"
