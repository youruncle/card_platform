import pytest

from card_platform.domain.serial_evidence import (
    serial_field_evidence,
    unique_visible_serial,
)


@pytest.mark.parametrize(
    ("evidence", "source_is_physical"),
    [
        (["20/20"], True),
        (["背面左侧竖排 20／20 清晰可见"], False),
        (["Card back foil stamp 20∕20 visible"], False),
        (["Updated front OCR: ２０／２０"], False),
        (["Front candidate clearly shows 20/20"], False),
        (["背面非常清晰可见的限编 20/20"], False),
        (["20", "/", "20"], True),
        (["serial stamp: 20 of 20"], False),
        (["Apple Vision 实卡背面 OCR，区域 [0.128, 0.534, 0.058, 0.142]：20/20"], False),
    ],
)
def test_visible_serial_accepts_physical_fraction_variants(
    evidence: list[str], source_is_physical: bool
) -> None:
    serial = unique_visible_serial(
        evidence,
        source_is_physical=source_is_physical,
    )

    assert serial is not None
    assert (serial.number, serial.total) == (20, 20)


@pytest.mark.parametrize(
    "evidence",
    [
        ["BORN: 3/6/73"],
        ["BORN16/19/78"],
        ["BORN: 2/1763"],
        ["Bane: 2/1763"],
        ["Bane: 2/17163"],
        ["Ht: 6 6 Wt:216\nBane:2/17163\nCollege: North Carolina\n局部 OCR：ппк:2/1763"],
        ["Bane: 2/17/63\n局部 OCR：Bant: 2/1763"],
        ["Bane: 2/17/63\n局部 OCR：2/1763"],
        ["5/24 was his debut date"],
        ["On 1//1/08\n局部 OCR：1/8"],
        ["On 1/|1/08"],
        ["On |/|1/08"],
        ["Charlotte, 3/30.\n局部 OCR：3/30"],
        ["No printed serial visible; 10/22/25 is the NBA debut date."],
        ["20/20 field goals"],
        ["20 of 20 points"],
        ["468\nBORN: 9/29/88 DRAFTED: 1st RD FROM: TEXAS\nFT%\n局部 OCR：380 /468 878 5626.6"],
        ["CAREER TOTALS\n79 260/428 341 794"],
        ["Card No. 20/20"],
        ["联网候选：5/24 编码格式为典型限编标识"],
        ["联网候选：目录有该平行。背面可见 20/20"],
        ["背面可见 20/20。TCDB checklist 将其列为限编"],
        ["手写套袋标记 8/23，并非卡面印刷限编"],
        ["根据卡号推测背面可能为 5/6"],
        ["根据卡号推测是限编。背面可见 5/6"],
        ["背面可见 5/6。This is probably the serial number"],
        ["OCR artifact 12/24B"],
        ["联网候选：Apple Vision 实卡背面 OCR，区域 [0.128, 0.534]：20/20"],
        ["Apple Vision 实卡背面 OCR，区域 [0.128, 0.534]：BORN 2/1763"],
    ],
)
def test_visible_serial_rejects_dates_stats_card_numbers_and_remote_claims(
    evidence: list[str],
) -> None:
    assert unique_visible_serial(evidence, source_is_physical=True) is None


def test_visible_stamp_survives_unrelated_birthday_on_same_card() -> None:
    serial = unique_visible_serial(
        [
            "Back text: BORN: 3/6/73, MELROSE PARK",
            "Front foil serial stamp: 020/020",
        ]
    )

    assert serial is not None
    assert (serial.number, serial.total) == (20, 20)


def test_later_explicit_serial_stamp_survives_collapsed_birthday() -> None:
    serial = unique_visible_serial(
        ["BORN: 2/1763; visible serial stamp: 065/125"],
        source_is_physical=True,
    )

    assert serial is not None
    assert (serial.number, serial.total) == (65, 125)


def test_birth_label_ocr_variants_do_not_reject_independent_serials() -> None:
    for evidence, expected in [
        ("Bane: 2/17/63; visible serial stamp: 2/1763", (2, 1763)),
        ("Front player Bane, foil stamp 15/99", (15, 99)),
        ("Card back serial stamp: 555/2025", (555, 2025)),
    ]:
        serial = unique_visible_serial([evidence])
        assert serial is not None
        assert (serial.number, serial.total) == expected


def test_twenty_of_twenty_is_not_a_plausible_month_day() -> None:
    serial = unique_visible_serial(["Back visible foil stamp 20/20; updated date © 2026"])

    assert serial is not None
    assert (serial.number, serial.total) == (20, 20)


def test_multiple_distinct_visible_fractions_are_ambiguous() -> None:
    assert unique_visible_serial(["背面印刷 20/20 清晰可见", "正面印刷 3/5 清晰可见"]) is None


def test_all_equivalent_field_evidence_is_collected() -> None:
    assert serial_field_evidence(
        {
            "is_numbered": ["背面：20/20"],
            "serial_number": ["OCR：20/20"],
            "serial_total": ["OCR：20/20"],
            "card_number": ["No. 105"],
        }
    ) == ["背面：20/20", "OCR：20/20", "OCR：20/20"]
