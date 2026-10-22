"""Per-view facts, not a model's repeated global verdict, control admission."""

from copy import deepcopy
import pytest

from test_player_price_image_evidence import fixture, admit


def faces():
    common = dict(
        complete=True,
        card_count=1,
        view_detail="whole single card and four edges visible",
        player_names=["Stephen Curry"],
        multi_subject=False,
        brand_name="Topps",
        product_name="Topps Chrome",
        identity_evidence=["printed STEPHEN CURRY / Topps Chrome on this face"],
        card_number=dict(
            value=None,
            number_role="unknown",
            detail="front artwork has no collector number printed",
        ),
        insert_name=None,
        surface=dict(
            rainbow="visible",
            fixed_colors=[],
            pattern="none",
            detail="front border shows a smooth rainbow glare with no fixed colour or etched pattern",
        ),
        finish_markings=[],
        finish_markings_complete=True,
        release_season=dict(value=None, detail="no actual release season printed on this face"),
        serial_stamp=dict(
            value=None,
            number_role="unknown",
            detail="whole surface shows no individual serial stamp",
        ),
        grade=dict(
            status="raw",
            company=None,
            score=None,
            cert_number=None,
            detail="whole bare card visible without slab or grading label",
        ),
        presence={
            name: dict(
                state="absent_in_view",
                detail=f"whole visible face has no {name} mark or major obstruction",
            )
            for name in [
                "autograph",
                "memorabilia",
                "patch",
                "redemption",
                "serial",
                "major_damage",
            ]
        },
        confidence="0.95",
        caveats=[],
    )
    front = deepcopy(common)
    front["role"] = "front"
    back = deepcopy(common)
    back.update(
        role="back",
        product_name="Chrome",
        card_number=dict(
            value="201",
            number_role="collector_number",
            detail="back upper-right independent printed card number 201",
        ),
        surface=dict(
            rainbow="unreadable",
            fixed_colors=None,
            pattern="unreadable",
            detail="back face does not expose the front optical finish",
        ),
        finish_markings=[
            dict(
                text="REFRACTOR",
                kind="variant_mark",
                detail="back above number: separate literal REFRACTOR mark",
            )
        ],
    )
    return [front, back]


def compose(raw):
    from card_platform.player_prices.image_observations import compose_faces

    return compose_faces(raw)


def test_standard_chrome_refractor_needs_both_front_surface_and_independent_reverse_mark():
    result = compose(faces())
    assert result.parallel_name == "Refractor" and result.card_number == "201"
    assert result.is_numbered is False and result.is_patch is False
    assert result.product_name == "Topps Chrome"
    assert result.face_observations[1]["product_name"] == "Chrome"
    assert admit(*fixture(result.model_dump(mode="json")))["status"] == "confirmed"


@pytest.mark.parametrize("front_mark", ["RED REFRACTOR", "X-FRACTOR", "REFRACTOR"])
def test_front_variant_mark_constrains_reverse_refractor_recipe(front_mark):
    raw = faces()
    raw[0]["finish_markings"] = [
        dict(
            text=front_mark,
            kind="variant_mark",
            detail=f"front lower-left independent version label reads {front_mark}",
        )
    ]
    result = compose(raw)
    if front_mark == "REFRACTOR":
        assert result.parallel_name == "Refractor"
        assert admit(*fixture(result.model_dump(mode="json")))["status"] == "confirmed"
    else:
        assert result.parallel_name is None
        assert admit(*fixture(result.model_dump(mode="json")))["status"] != "confirmed"


@pytest.mark.parametrize(
    "change",
    [
        "pattern",
        "colour",
        "mark_missing",
        "marks_unknown",
        "rainbow_unknown",
        "rainbow_not_visible",
    ],
)
def test_an_unknown_or_more_specific_surface_never_becomes_plain_refractor(change):
    raw = faces()
    if change == "pattern":
        raw[0]["surface"]["pattern"] = "cracked_ice"
    elif change == "colour":
        raw[0]["surface"]["fixed_colors"] = ["red"]
    elif change == "mark_missing":
        raw[1]["finish_markings"] = []
    elif change == "marks_unknown":
        raw[1]["finish_markings_complete"] = False
    elif change == "rainbow_unknown":
        raw[0]["surface"]["rainbow"] = "unreadable"
    else:
        raw[0]["surface"]["rainbow"] = "not_visible"
    result = compose(raw)
    assert result.parallel_name is None
    assert admit(*fixture(result.model_dump(mode="json")))["status"] != "confirmed"


@pytest.mark.parametrize(
    "state,expected", [("present", True), ("absent_in_view", False), ("unreadable", None)]
)
def test_one_face_presence_combines_without_copying_it_to_the_other_face(state, expected):
    raw = faces()
    raw[0]["presence"]["autograph"]["state"] = state
    result = compose(raw)
    assert result.is_autograph is expected
    values = {e.image_index: e.value for e in result.physical_evidence if e.field == "is_autograph"}
    assert values == {0: expected, 1: False}


def test_omitted_presence_is_unknown_and_patch_does_not_fill_missing_memorabilia():
    raw = faces()
    raw[0]["presence"].pop("patch")
    result = compose(raw)
    assert result.is_patch is None
    raw = faces()
    raw[0]["presence"]["patch"]["state"] = "present"
    assert admit(*fixture(compose(raw).model_dump(mode="json")))["status"] != "confirmed"


def test_only_major_visible_damage_controls_comparable_condition_not_guarantees_about_hidden_flaws():
    raw = faces()
    raw[0]["presence"]["major_damage"].update(
        state="unreadable", detail="major area blocked by opaque holder; cannot inspect folds"
    )
    result = compose(raw)
    assert result.condition_status == "uncertain"
    assert admit(*fixture(result.model_dump(mode="json")))["status"] != "confirmed"


def test_real_serial_is_read_from_one_face_and_preserves_individual_copy():
    raw = faces()
    raw[1]["presence"]["serial"]["state"] = "present"
    raw[1]["serial_stamp"].update(
        value="18/99",
        number_role="unique_serial",
        detail="back lower-right actual machine stamped 18/99",
    )
    result = compose(raw)
    assert (result.is_numbered, result.serial_number, result.serial_total) == (True, 18, 99)
    assert (
        result.parallel_name is None
    )  # a standard unnumbered recipe cannot relabel a stamped variant


def test_mismatched_subject_or_two_fronts_keeps_pair_unverified():
    for kind in ("subject", "role"):
        raw = faces()
        if kind == "subject":
            raw[1]["player_names"] = ["LeBron James"]
        else:
            raw[1]["role"] = "front"
        assert admit(*fixture(compose(raw).model_dump(mode="json")))["status"] != "confirmed"


def test_reversing_upload_order_does_not_move_or_copy_evidence():
    result = compose(list(reversed(faces())))
    assert result.card_number == "201" and result.parallel_name == "Refractor"
    assert (
        next(
            e for e in result.physical_evidence if e.field == "card_number" and e.value == "201"
        ).image_index
        == 0
    )
    assert admit(*fixture(result.model_dump(mode="json")))["status"] == "confirmed"


def test_a_composed_verdict_loses_authority_if_the_original_model_observation_blob_changes():
    obs, receipt, blobs, official = fixture(compose(faces()).model_dump(mode="json"))
    assert admit(obs, receipt, blobs, official)["status"] == "confirmed"
    blobs[receipt["usage"]["observation_trace_sha256"]] = b'{"changed":"paid model response lost"}'
    assert admit(obs, receipt, blobs, official)["status"] != "confirmed"


def test_prizm_silver_and_plain_base_remain_separate_from_a_named_texture():
    for rainbow, markers, expected in [
        ("visible", ["PRIZM"], "Silver Prizm"),
        ("not_visible", [], "Base"),
        ("visible", [], None),
    ]:
        raw = faces()
        for f in raw:
            f.update(brand_name="Panini", product_name="Prizm")
        raw[0]["surface"]["rainbow"] = rainbow
        raw[1]["finish_markings"] = [
            dict(
                text=m,
                kind="variant_mark",
                detail="separate back version mark outside copyright product line",
            )
            for m in markers
        ]
        assert compose(raw).parallel_name == expected
        raw[0]["surface"]["pattern"] = "cracked_ice"
        assert compose(raw).parallel_name is None


def test_reverse_named_insert_cannot_disappear_into_an_ordinary_base_identity():
    raw = faces()
    raw[1]["insert_name"] = "Finalists"
    raw[1]["identity_evidence"].append("back clearly printed Finalists as the insert series")
    result = compose(raw)
    assert result.insert_name == "Finalists"
    assert admit(*fixture(result.model_dump(mode="json")))["status"] != "confirmed"


@pytest.mark.parametrize(
    "detail",
    [
        "back central Panini Prizm product logo and copyright product line",
        "背面产品Logo PRIZM，位于版权产品行中，未看到独立版本标识",
    ],
)
def test_a_product_or_copyright_word_cannot_become_a_physical_variant_mark(detail):
    raw = faces()
    for f in raw:
        f.update(brand_name="Panini", product_name="Prizm")
    raw[1]["finish_markings"] = [dict(text="PRIZM", detail=detail, kind="variant_mark")]
    assert compose(raw).parallel_name is None


@pytest.mark.parametrize("finish", ["Base", "Refractor", "Silver Prizm"])
def test_recording_known_product_logos_does_not_hide_a_separate_finish(finish):
    raw = faces()
    if finish != "Refractor":
        for face in raw:
            face.update(brand_name="Panini", product_name="Prizm")
        raw[1]["finish_markings"] = (
            [dict(text="PRIZM", kind="variant_mark", detail="back separate actual PRIZM label")]
            if finish == "Silver Prizm"
            else []
        )
    if finish == "Base":
        raw[0]["surface"]["rainbow"] = "not_visible"
    brand, product = raw[0]["brand_name"], raw[0]["product_name"]
    raw[0]["finish_markings"].append(
        dict(text=product, kind="product_logo", detail="front lower corner actual product logo")
    )
    raw[1]["finish_markings"].extend(
        [
            dict(text=brand, kind="product_logo", detail="back actual brand product logo"),
            dict(
                text=f"© 2024 {brand} All rights reserved.",
                kind="copyright",
                detail="back bottom copyright rights-reservation line",
            ),
        ]
    )
    assert compose(raw).parallel_name == finish


@pytest.mark.parametrize("kind", ["product_logo", "copyright", "unknown"])
def test_a_special_variant_cannot_be_ignored_by_misclassifying_it_as_a_logo(kind):
    raw = faces()
    raw[0]["finish_markings"] = [
        dict(text="RED REFRACTOR", kind=kind, detail="front lower-left literal RED REFRACTOR")
    ]
    assert compose(raw).parallel_name is None


def test_a_known_product_logo_alone_still_cannot_prove_a_silver_finish():
    raw = faces()
    for face in raw:
        face.update(brand_name="Panini", product_name="Prizm")
    raw[1]["finish_markings"] = [
        dict(text="PRIZM", kind="product_logo", detail="back central actual Prizm product logo")
    ]
    assert compose(raw).parallel_name is None


def test_short_chinese_copyright_location_is_not_a_missing_physical_observation():
    raw = faces()
    raw[1]["finish_markings"].extend(
        [
            dict(
                text="© 2025 Topps All rights reserved.", kind="copyright", detail="底部竖排版权行"
            ),
            dict(text="© 2025 NBA Properties Inc.", kind="copyright", detail="背面底部联盟版权行"),
        ]
    )
    result = compose(raw)
    assert result.parallel_name == "Refractor"
    assert admit(*fixture(result.model_dump(mode="json")))["status"] == "confirmed"


@pytest.mark.parametrize("extra", ["红折", "碎冰", "18/99", "18", "99", "2024-99"])
def test_copyright_classification_cannot_hide_nonlegal_text_or_a_serial_fraction(extra):
    raw = faces()
    raw[0]["finish_markings"] = [
        dict(text=f"© 2025 Topps {extra}", kind="copyright", detail="front actual literal stamp")
    ]
    result = compose(raw)
    assert result.parallel_name is None
    assert admit(*fixture(result.model_dump(mode="json")))["status"] != "confirmed"


def test_logo_classification_cannot_erase_an_explicit_independent_variant_role():
    raw = faces()
    for face in raw:
        face.update(brand_name="Panini", product_name="Prizm")
    raw[0]["surface"]["rainbow"] = "not_visible"
    raw[1]["finish_markings"] = [
        dict(
            text="PRIZM",
            kind="product_logo",
            detail="back separate PRIZM variant mark; not product logo",
        )
    ]
    result = compose(raw)
    assert result.parallel_name is None
    assert admit(*fixture(result.model_dump(mode="json")))["status"] != "confirmed"


def test_nonempty_serial_observation_with_unknown_role_is_not_an_absence_of_numbering():
    raw = faces()
    raw[1]["serial_stamp"].update(
        value="18/99",
        number_role="unknown",
        detail="back actual individual machine stamp 18/99; collector role uncertain",
    )
    result = compose(raw)
    assert result.is_numbered is None
    assert admit(*fixture(result.model_dump(mode="json")))["status"] != "confirmed"


def test_reviewed_diacritic_subject_aliases_are_canonicalized_only_for_the_pair_gate():
    raw = faces()
    raw[0]["player_names"] = ["Luka Dončić"]
    raw[1]["player_names"] = ["Luka Doncic"]
    result = compose(raw)
    assert result.player_name == "Luka Dončić"
    assert result.pair_validation.front.player_names == ["Luka Dončić"]
    assert result.pair_validation.back.player_names == ["Luka Dončić"]
    assert result.face_observations[0]["player_names"] == ["Luka Dončić"]
