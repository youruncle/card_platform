"""A title/official number cannot replace bound physical-card evidence."""

import hashlib
import json
from io import BytesIO

from PIL import Image
import pytest

from test_player_market import observation
from test_player_price_catalog import catalog, fact


FRONT = "https://images.cardhobby.com/test.jpg"
BACK = "https://images.cardhobby.com/back.jpg"
HTML = (
    f'<a href="/market/bidrecords/123"><span id="bidcount">2</span></a>'
    f'<div id="layer-photos-demo"><img src="{FRONT}"><img src="{BACK}"></div>'
)


def photo(color):
    out = BytesIO()
    Image.new("RGB", (48, 64), color).save(out, format="PNG")
    return out.getvalue()


def visual(**changes):
    front = dict(
        role="front",
        player_names=["Stephen Curry"],
        brand_name="Topps",
        product_name="Topps Chrome",
        card_number=None,
        multi_subject=False,
        evidence=["front printed STEPHEN CURRY and Topps Chrome"],
    )
    back = dict(
        role="back",
        player_names=["Stephen Curry"],
        brand_name="Topps",
        product_name="Topps Chrome",
        card_number="201",
        multi_subject=False,
        evidence=["back printed STEPHEN CURRY / CHROME / 201"],
    )
    result = dict(
        player_name="Stephen Curry",
        brand_name="Topps",
        product_name="Topps Chrome",
        card_number="201",
        season_start=None,
        season_end=None,
        parallel_name="Refractor",
        is_autograph=False,
        is_memorabilia=False,
        is_patch=False,
        is_redemption=False,
        is_numbered=False,
        grade_company=None,
        grade_value=None,
        grade_status="raw",
        front_complete=True,
        back_complete=True,
        card_counts=[1, 1],
        condition_status="ordinary_visible",
        confidence="0.94",
        pair_validation=dict(
            status="consistent",
            front=front,
            back=back,
            evidence=["independent same subject/product"],
        ),
        physical_evidence=[],
    )
    for field, value in dict(
        is_autograph=False,
        is_memorabilia=False,
        is_patch=False,
        is_redemption=False,
        is_numbered=False,
        grade_status="raw",
        condition_status="ordinary_visible",
    ).items():
        for index in [0, 1]:
            result["physical_evidence"].append(
                dict(
                    field=field,
                    value=value,
                    image_index=index,
                    detail=f"whole card face {index}: directly observed {field}={value}; no hidden edge",
                )
            )
    result["physical_evidence"] += [
        dict(
            field="card_count",
            value=1,
            image_index=0,
            detail="front image contains exactly one card, no other trading cards",
        ),
        dict(
            field="card_count",
            value=1,
            image_index=1,
            detail="back image contains exactly one card, no other trading cards",
        ),
        dict(
            field="card_number",
            value="201",
            image_index=1,
            number_role="collector_number",
            detail="back separate upper-right card number 201",
        ),
        dict(
            field="parallel_name",
            value="Refractor",
            image_index=0,
            detail="front smooth rainbow refractor border; no fixed coloured pattern",
        ),
        dict(
            field="parallel_name",
            value="Refractor",
            image_index=1,
            detail="back below card number: literal REFRACTOR",
        ),
    ]
    result.update(changes)
    return result


def fixture(result=None, *, title=None, html=HTML):
    from card_platform.player_prices.image_evidence import make_receipt

    obs = observation(title=title or "2025-26 Topps Chrome Stephen Curry 银折 #132B#AIR#XBW10#")
    images = [(FRONT, photo("white"), "image/png"), (BACK, photo("gray"), "image/png")]
    blobs = {hashlib.sha256(content).hexdigest(): content for _, content, _ in images}
    blobs[hashlib.sha256(html.encode()).hexdigest()] = html.encode()
    parsed = result or visual()
    usage = {}
    if parsed.get("observation_contract"):
        trace = dict(
            contract="price-image-model-trace-v1",
            model_calls=2,
            observations=[
                dict(
                    image_index=i,
                    image_sha256=hashlib.sha256(images[i][1]).hexdigest(),
                    model="deepseek-flash",
                    request_extra={"thinking": {"type": "disabled"}},
                    raw_response=json.dumps(face),
                )
                for i, face in enumerate(parsed["face_observations"])
            ],
        )
        data = json.dumps(trace).encode()
        sha = hashlib.sha256(data).hexdigest()
        blobs[sha] = data
        usage["observation_trace_sha256"] = sha
    receipt = make_receipt(obs, html, images, parsed, model="deepseek-flash", usage=usage)
    official = catalog(
        [
            fact(
                brand="Topps",
                product="Topps Chrome",
                season_start=2025,
                season_end=2026,
                card_number="201",
            )
        ]
    )
    return obs, receipt, blobs, official


def admit(obs, receipt, blobs, official):
    from card_platform.player_prices.image_evidence import admit_receipt

    return admit_receipt(obs, receipt, "Stephen Curry", official, blob_reader=blobs.__getitem__)


def test_missing_title_number_is_resolved_by_independent_back_ocr_without_rewriting_sale():
    obs, receipt, blobs, official = fixture()
    decision = admit(obs, receipt, blobs, official)
    assert decision["status"] == "confirmed"
    assert decision["identity"]["card_number"] == "201"
    assert decision["identity"]["parallel_name"] == "Refractor"
    assert decision["identity"]["season_start"] == 2025
    assert decision["identity"]["image_evidence"]["receipt_sha256"] == receipt["receipt_sha256"]
    assert obs["sale"]["title"].endswith("#132B#AIR#XBW10#")
    assert "parallel_name" not in decision["identity"]["catalog_evidence"]["fields"]


@pytest.mark.parametrize(
    "change",
    [
        dict(is_numbered=None),
        dict(grade_status="unknown"),
        dict(back_complete=False),
        dict(parallel_name=None),
        dict(condition_status="uncertain"),
        dict(is_autograph=None),
        dict(is_memorabilia=None),
        dict(is_patch=None),
        dict(is_redemption=None),
        dict(confidence="0.5"),
    ],
)
def test_unknown_physical_fields_never_become_confirmed_from_a_checklist(change):
    args = fixture(visual(**change))
    assert admit(*args)["status"] != "confirmed"


@pytest.mark.parametrize(
    "kind", ["receipt", "blob", "new_observation", "gallery_item", "duplicate_photo"]
)
def test_binding_changes_cannot_reuse_the_old_visual_authority(kind):
    obs, receipt, blobs, official = fixture()
    if kind == "receipt":
        receipt["result"]["parallel_name"] = "Base"
    elif kind == "blob":
        blobs[receipt["images"][1]["sha256"]] = b"changed"
    elif kind == "new_observation":
        obs = observation(title="2025-26 Topps Chrome Stephen Curry #201 base", price=999)
    elif kind == "gallery_item":
        from card_platform.player_prices.image_evidence import make_receipt

        with pytest.raises(ValueError):
            make_receipt(
                obs,
                HTML.replace("/123", "/456"),
                [(FRONT, photo("white"), "image/png"), (BACK, photo("gray"), "image/png")],
                visual(),
                model="deepseek-flash",
                usage={},
            )
        return
    else:
        from card_platform.player_prices.image_evidence import make_receipt

        with pytest.raises(ValueError):
            make_receipt(
                obs,
                HTML,
                [(FRONT, photo("white"), "image/png"), (BACK, photo("white"), "image/png")],
                visual(),
                model="deepseek-flash",
                usage={},
            )
        return
    assert admit(obs, receipt, blobs, official)["status"] != "confirmed"


@pytest.mark.parametrize(
    "title",
    [
        "2025-26 Topps Chrome Stephen Curry #99 银折",
        "2024-25 Topps Chrome Stephen Curry #201 银折",
        "2025-26 Panini Prizm Stephen Curry #201 银折",
        "2025-26 Topps Chrome Stephen Curry #201 红折",
        "2025-26 Topps Chrome Stephen Curry #201 银折 PSA10",
        "2025-26 Topps Chrome Stephen Curry #201 银折 /99",
        "2025-26 Topps Chrome Stephen Curry #201 银折 卡面折痕",
    ],
)
def test_images_may_fill_missing_fields_but_cannot_erase_explicit_title_conflicts(title):
    args = fixture(title=title)
    assert admit(*args)["status"] != "confirmed"


def test_pair_and_field_evidence_must_independently_cover_the_uploaded_faces():
    wrong = visual()
    wrong["pair_validation"]["back"]["player_names"] = ["LeBron James"]
    assert admit(*fixture(wrong))["status"] != "confirmed"
    missing = visual()
    missing["physical_evidence"] = [
        e for e in missing["physical_evidence"] if e["field"] != "card_number"
    ]
    assert admit(*fixture(missing))["status"] != "confirmed"
    misplaced = visual()
    next(e for e in misplaced["physical_evidence"] if e["field"] == "card_number")[
        "image_index"
    ] = 0
    assert admit(*fixture(misplaced))["status"] != "confirmed"


def test_jersey_or_serial_fraction_is_not_an_independent_card_number():
    wrong = visual()
    next(e for e in wrong["physical_evidence"] if e["field"] == "card_number")["detail"] = (
        "back jersey number 201"
    )
    assert admit(*fixture(wrong))["status"] != "confirmed"
    serial = visual()
    next(e for e in serial["physical_evidence"] if e["field"] == "card_number")["detail"] = (
        "back serial stamp 201/299"
    )
    assert admit(*fixture(serial))["status"] != "confirmed"


def test_malformed_field_observation_is_not_a_printed_number_transcription():
    wrong = visual()
    next(e for e in wrong["physical_evidence"] if e["field"] == "card_number")["value"] = {
        "201": ""
    }
    with pytest.raises(ValueError):
        fixture(wrong)


@pytest.mark.parametrize(
    "field,value",
    [
        ("parallel_name", "Red Refractor"),
        ("condition_status", "material_damage"),
        ("is_numbered", True),
        ("card_count", 2),
    ],
)
def test_a_matching_field_cannot_hide_another_explicit_conflicting_observation(field, value):
    result = visual()
    result["physical_evidence"].append(
        dict(
            field=field,
            value=value,
            image_index=0,
            detail="a separate explicit contradictory physical observation on the front",
        )
    )
    assert admit(*fixture(result))["status"] != "confirmed"


def test_extra_named_subject_is_rejected_even_when_model_omits_multi_subject_flag():
    result = visual()
    for face in ("front", "back"):
        result["pair_validation"][face]["player_names"].append("LeBron James")
    assert admit(*fixture(result))["status"] != "confirmed"


def test_unobserved_multi_subject_field_does_not_inherit_shared_false_default():
    result = visual()
    result["pair_validation"]["front"].pop("multi_subject")
    args = fixture(result)
    assert args[1]["result"]["pair_validation"]["front"]["multi_subject"] is None
    assert admit(*args)["status"] != "confirmed"


def test_patch_and_non_memorabilia_is_an_explicit_physical_conflict_not_a_default_fill():
    result = visual(is_patch=True)
    for e in result["physical_evidence"]:
        if e["field"] == "is_patch":
            e["value"] = True
    assert (
        admit(*fixture(result, title="2025-26 Topps Chrome Stephen Curry #201 银折 patch"))[
            "status"
        ]
        != "confirmed"
    )


def test_unreadable_front_number_is_not_a_conflicting_back_number_observation():
    result = visual()
    result["physical_evidence"].append(
        dict(
            field="card_number",
            value=None,
            image_index=0,
            number_role="unknown",
            detail="front artwork has no printed collector number; number is readable only on back",
        )
    )
    assert admit(*fixture(result))["status"] == "confirmed"


@pytest.mark.parametrize(
    "field,index", [("card_number", 1), ("is_autograph", 0), ("is_numbered", 1)]
)
def test_unknown_on_a_required_face_cannot_hide_behind_a_second_positive_row(field, index):
    result = visual()
    result["physical_evidence"].append(
        dict(
            field=field,
            value=None,
            image_index=index,
            detail="this required observation is unreadable on this face",
        )
    )
    assert admit(*fixture(result))["status"] != "confirmed"


@pytest.mark.parametrize(
    "value,detail,role",
    [
        ("2/01", "back lower corner stamped serial fraction 2/01", "collector_number"),
        ("201", "back lower-right serial number 201 of 299", "collector_number"),
        ("201", "back statistics 201 points", "collector_number"),
        ("201", "back 201 is serial number, not a collector number", "collector_number"),
        ("201", "back 201 is statistics, not the collector number", "collector_number"),
        ("201", "back 201 是限编流水号，不是卡号", "collector_number"),
        ("201", "back separate upper-right card number 201", "unknown"),
        ("201", "back separate upper-right card number 201", None),
    ],
)
def test_collector_number_needs_exact_transcription_and_an_independent_non_statistical_role(
    value, detail, role
):
    result = visual()
    field = next(e for e in result["physical_evidence"] if e["field"] == "card_number")
    field.update(value=value, detail=detail, number_role=role)
    assert admit(*fixture(result))["status"] != "confirmed"


def test_bound_images_cannot_resolve_an_explicit_compound_finish_as_one_component():
    result = visual(brand_name="Panini", product_name="Prizm", parallel_name="Blue Prizm")
    for face in ("front", "back"):
        result["pair_validation"][face].update(brand_name="Panini", product_name="Prizm")
    for field in result["physical_evidence"]:
        if field["field"] == "parallel_name":
            field.update(value="Blue Prizm", detail="face has a fixed solid blue prizm border")
    obs, receipt, blobs, _ = fixture(
        result, title="2025-26 Panini Prizm Stephen Curry #201 红白蓝折射"
    )
    official = catalog([fact(season_start=2025, season_end=2026, card_number="201")])
    assert admit(obs, receipt, blobs, official)["status"] != "confirmed"


def numbered_visual(number=18, total=99):
    result = visual(is_numbered=True, serial_number=number, serial_total=total)
    for field in result["physical_evidence"]:
        if field["field"] == "is_numbered":
            field.update(value=True, detail=f"physical stamp {number}/{total} on this face")
    result["physical_evidence"] += [
        dict(
            field=field,
            value=value,
            image_index=1,
            detail=f"back lower corner actual stamped serial {number}/{total}",
        )
        for field, value in (("serial_number", number), ("serial_total", total))
    ]
    return result


@pytest.mark.parametrize("other_value", [None, False, "absent"])
def test_a_real_serial_stamp_on_one_face_does_not_need_to_be_invented_on_the_other(other_value):
    result = numbered_visual()
    result["physical_evidence"] = [
        e
        for e in result["physical_evidence"]
        if not (e["field"] == "is_numbered" and e["image_index"] == 0)
    ]
    if other_value != "absent":
        result["physical_evidence"].append(
            dict(
                field="is_numbered",
                image_index=0,
                value=other_value,
                detail="front full face shows no serial stamp; back has actual stamp",
            )
        )
    assert (
        admit(*fixture(result, title="2025-26 Topps Chrome Stephen Curry #201 银折 18/99"))[
            "status"
        ]
        == "confirmed"
    )


def test_source_individual_serial_is_bound_to_the_physical_copy_not_just_its_total():
    result = numbered_visual()
    assert (
        admit(*fixture(result, title="2025-26 Topps Chrome Stephen Curry #201 银折 18/99"))[
            "status"
        ]
        == "confirmed"
    )
    assert (
        admit(*fixture(result, title="2025-26 Topps Chrome Stephen Curry #201 银折 12/99"))[
            "status"
        ]
        != "confirmed"
    )


def graded_visual(cert="12345678"):
    result = visual(
        grade_status="graded", grade_company="PSA", grade_value="10", grade_cert_number=cert
    )
    for field in result["physical_evidence"]:
        if field["field"] == "grade_status":
            field.update(value="graded", detail="whole slab visible, rigid PSA label and holder")
    result["physical_evidence"] += [
        dict(
            field=field,
            value=value,
            image_index=0,
            detail=f"front slab label explicitly printed {field}: {value}",
        )
        for field, value in (
            ("grade_company", "PSA"),
            ("grade_value", "10"),
            ("grade_cert_number", cert),
        )
    ]
    return result


def test_graded_copy_requires_actual_certificate_evidence_and_raw_cannot_contain_one():
    assert (
        admit(
            *fixture(graded_visual(), title="2025-26 Topps Chrome Stephen Curry #201 银折 PSA10")
        )["status"]
        == "confirmed"
    )
    assert (
        admit(
            *fixture(
                graded_visual(None), title="2025-26 Topps Chrome Stephen Curry #201 银折 PSA10"
            )
        )["status"]
        != "confirmed"
    )
    assert admit(*fixture(visual(grade_cert_number="12345678")))["status"] != "confirmed"


@pytest.mark.parametrize("kind", ["raw", "numbered", "graded"])
def test_back_first_gallery_preserves_each_physical_observation_and_label_binding(kind):
    if kind == "numbered":
        result = numbered_visual()
        title = "2025-26 Topps Chrome Stephen Curry #201 银折 18/99"
    elif kind == "graded":
        result = graded_visual()
        title = "2025-26 Topps Chrome Stephen Curry #201 银折 PSA10"
    else:
        result = visual()
        title = None
    pair = result["pair_validation"]
    pair["front"], pair["back"] = pair["back"], pair["front"]
    for field in result["physical_evidence"]:
        field["image_index"] = 1 - field["image_index"]
    assert admit(*fixture(result, title=title))["status"] == "confirmed"


def test_explicit_source_grading_certificate_must_identify_the_same_slab():
    title = "2025-26 Topps Chrome Stephen Curry #201 银折 PSA10 证书号"
    assert admit(*fixture(graded_visual(), title=title + "12345678"))["status"] == "confirmed"
    assert admit(*fixture(graded_visual(), title=title + "87654321"))["status"] != "confirmed"


@pytest.mark.parametrize(
    "suffix", ["certified", "certified authentic", "certification number 12345678"]
)
def test_authenticity_words_are_not_misread_as_a_different_grading_certificate(suffix):
    assert (
        admit(
            *fixture(
                graded_visual(),
                title="2025-26 Topps Chrome Stephen Curry #201 银折 PSA10 " + suffix,
            )
        )["status"]
        == "confirmed"
    )


@pytest.mark.parametrize("counts", [[2, 1], [1, 2], [None, 1]])
def test_same_subject_in_a_multiple_card_photo_is_not_a_single_card_sale(counts):
    assert admit(*fixture(visual(card_counts=counts)))["status"] != "confirmed"


def test_allowed_primary_thumbnail_transform_binds_to_the_same_original_gallery_image():
    from card_platform.public_source_catalog import observe_public_sale
    from card_platform.player_prices.image_evidence import make_receipt

    obs, _, blobs, official = fixture()
    obs = observe_public_sale({**obs["raw_row"], "TitImg": FRONT + "?imageView2/2/w/800"})
    receipt = make_receipt(
        obs,
        HTML,
        [(FRONT, photo("white"), "image/png"), (BACK, photo("gray"), "image/png")],
        visual(),
        model="deepseek-flash",
        usage={},
    )
    assert admit(obs, receipt, blobs, official)["status"] == "confirmed"


def test_archived_bytes_are_content_addressed_and_cannot_be_replaced_silently(tmp_path):
    from card_platform.player_prices.image_reader import EvidenceBlobs

    store = EvidenceBlobs(tmp_path / "evidence")
    content = photo("blue")
    sha = store.put(content)
    assert sha == hashlib.sha256(content).hexdigest()
    assert store.put(content) == sha and store.read(sha) == content
    with pytest.raises(ValueError):
        store.read("../../private")
    blob = tmp_path / "evidence" / (sha + ".blob")
    blob.write_bytes(b"changed")
    with pytest.raises(ValueError):
        store.read(sha)
