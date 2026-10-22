"""Immutable public-image observations; missing source fields need actual images."""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, StrictBool, StrictInt

from card_platform.domain.listing_condition import condition_rejection_reasons
from card_platform.domain.number_roles import non_catalog_number_role, serial_non_unique_role
from card_platform.domain.season import explicit_seasons
from card_platform.ingestion.cardhobby import assess_cardhobby_match
from card_platform.ingestion.cardhobby_public_item import (
    canonical_public_item_url,
    parse_public_item_html,
    public_gallery_for_primary,
)
from card_platform.knowledge_card_catalog import text_key, product_key
from card_platform.model_requests import nonthinking_extra_body
from card_platform.pair_integrity import derive_pair_validation
from card_platform.player_prices.discovery import (
    identity_title,
    source_finish_conflict,
    _plain_base_title,
)
from card_platform.player_prices.subjects import canonical_subject_name
from card_platform.public_source_catalog import validated_sale
from card_platform.purchase_cost_images import validate_photo
from card_platform.schemas import CardPairValidation, CardFaceObservation

CONTRACT = "player-price-image-evidence-v1"
RESULT_CONTRACT = "player-price-physical-observation-v1"
PHYSICAL_ATTRIBUTES = {"is_autograph", "is_memorabilia", "is_patch", "is_redemption", "is_numbered"}


class PhysicalEvidence(BaseModel):
    field: str = Field(min_length=1, max_length=40)
    image_index: Literal[0, 1]
    value: str | bool | int | float | None
    detail: str = Field(min_length=8, max_length=600)
    number_role: (
        Literal[
            "collector_number",
            "serial_number",
            "jersey",
            "statistics",
            "production_code",
            "unknown",
        ]
        | None
    ) = None


class PriceFaceObservation(CardFaceObservation):
    multi_subject: StrictBool | None = None


class PricePairValidation(CardPairValidation):
    front: PriceFaceObservation = Field(default_factory=PriceFaceObservation)
    back: PriceFaceObservation = Field(default_factory=PriceFaceObservation)


class PriceImageResult(BaseModel):
    # Keep null as null. General recognition's convenience defaults (e.g.
    # memorabilia=false => patch=false) are not independent visual evidence.
    player_name: str | None = None
    brand_name: str | None = None
    product_name: str | None = None
    card_number: str | None = None
    insert_name: str | None = None
    parallel_name: str | None = None
    season_start: int | None = Field(default=None, ge=1940, le=2200)
    season_end: int | None = Field(default=None, ge=1940, le=2200)
    is_autograph: StrictBool | None = None
    is_memorabilia: StrictBool | None = None
    is_patch: StrictBool | None = None
    is_redemption: StrictBool | None = None
    is_numbered: StrictBool | None = None
    serial_number: int | None = Field(default=None, ge=1, strict=True)
    serial_total: int | None = Field(default=None, ge=1, strict=True)
    grade_company: str | None = None
    grade_value: Decimal | None = Field(default=None, ge=0, le=10)
    grade_cert_number: str | None = None
    pair_validation: PricePairValidation | None = None
    confidence: Decimal = Field(ge=0, le=1)
    caveats: list[str] = Field(default_factory=list)
    grade_status: Literal["raw", "graded", "unknown"] = "unknown"
    front_complete: bool = False
    back_complete: bool = False
    card_counts: list[StrictInt | None] = Field(
        default_factory=lambda: [None, None], min_length=2, max_length=2
    )
    condition_status: Literal["ordinary_visible", "material_damage", "uncertain"] = "uncertain"
    physical_evidence: list[PhysicalEvidence] = Field(default_factory=list, max_length=60)
    observation_contract: Literal["price-per-face-observation-v1"] | None = None
    identity_derivation_contract: Literal["price-pair-manufacturer-v2"] | None = None
    face_observations: list[dict] = Field(default_factory=list, max_length=2)
    derivations: list[dict] = Field(default_factory=list)


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _seal(value):
    return _hash(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    )


def image_pair_key(receipt):
    """A lookup key only; byte/source/trace binding still requires validation."""
    try:
        images = receipt["images"]
        hashes = [image["sha256"] for image in images]
        if (
            not isinstance(images, list)
            or len(hashes) != 2
            or len(set(hashes)) != 2
            or any(
                not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha) for sha in hashes
            )
        ):
            return None
        return tuple(sorted(hashes))
    except (KeyError, TypeError, AttributeError):
        return None


def gallery_primary(gallery):
    # Only call after public_gallery_for_primary has validated the raw API
    # thumbnail query and proved membership in this exact item's gallery.
    url = urlsplit(gallery["primary_image_url"])
    primary = urlunsplit((url.scheme, url.netloc, url.path, "", ""))
    if primary not in gallery["image_urls"]:
        raise ValueError("unbound_price_primary")
    return primary


def make_receipt(observation, html, images, result, *, model, usage, now=None):
    """Caller archives these exact HTML/image bytes before storing the receipt."""
    sale, _ = validated_sale(observation)
    now = now or datetime.now(timezone.utc)
    if model != "deepseek-flash" or now.tzinfo is None or now > datetime.now(timezone.utc):
        raise ValueError("invalid_price_vision_context")
    payload = parse_public_item_html(html, sale.source_url, now=now)
    gallery = public_gallery_for_primary(payload, sale.source_url, sale.image_url)
    if (
        not gallery
        or len(gallery["image_urls"]) != 2
        or len(images) != 2
        or gallery_primary(gallery) not in [image[0] for image in images]
    ):
        raise ValueError("unbound_price_gallery")
    bindings = []
    for url, content, mime in images:
        if url not in gallery["image_urls"] or mime not in {
            "image/jpeg",
            "image/png",
            "image/webp",
        }:
            raise ValueError("unbound_price_image")
        validate_photo(content)
        bindings.append(dict(url=url, sha256=_hash(content), bytes=len(content), content_type=mime))
    if len({b["sha256"] for b in bindings}) != 2 or len({b["url"] for b in bindings}) != 2:
        raise ValueError("duplicate_price_images")
    parsed = PriceImageResult.model_validate(result)
    body = dict(
        contract=CONTRACT,
        result_contract=RESULT_CONTRACT,
        source_id="item:" + sale.item_id,
        source_url=canonical_public_item_url(sale.source_url),
        observation_sha256=observation["raw_row_sha256"],
        captured_at=now.isoformat(),
        gallery=gallery,
        images=bindings,
        result=parsed.model_dump(mode="json"),
        model=model,
        request_extra=nonthinking_extra_body(model),
        usage=usage,
    )
    return {**body, "receipt_sha256": _seal(body)}


def _read_bound_images(sale, captured, html_sha256, images, blob_reader, *, expected_gallery=None):
    """Shared photo binding; this grants no identity or model authority."""
    if captured.tzinfo is None or captured > datetime.now(timezone.utc):
        raise ValueError("price_gallery_capture_time_invalid")
    html = blob_reader(html_sha256)
    if _hash(html) != html_sha256:
        raise ValueError("price_gallery_bytes_changed")
    gallery = public_gallery_for_primary(
        parse_public_item_html(html.decode(), sale.source_url, now=captured),
        sale.source_url,
        sale.image_url,
    )
    if (
        not gallery
        or (expected_gallery is not None and gallery != expected_gallery)
        or len(gallery["image_urls"]) != 2
        or len(images) != 2
        or len({image["url"] for image in images}) != 2
    ):
        raise ValueError("price_gallery_binding_changed")
    seen = set()
    for image in images:
        content = blob_reader(image["sha256"])
        if (
            image["url"] not in gallery["image_urls"]
            or image["content_type"] not in {"image/jpeg", "image/png", "image/webp"}
            or _hash(content) != image["sha256"]
            or len(content) != image["bytes"]
            or image["sha256"] in seen
        ):
            raise ValueError("price_image_bytes_changed")
        validate_photo(content)
        seen.add(image["sha256"])
    if gallery_primary(gallery) not in [image["url"] for image in images]:
        raise ValueError("price_primary_image_changed")


def _read_bound_receipt(observation, receipt, blob_reader):
    sale, _ = validated_sale(observation)
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    captured = datetime.fromisoformat(body["captured_at"])
    if (
        body["contract"] != CONTRACT
        or body["result_contract"] != RESULT_CONTRACT
        or _seal(body) != receipt["receipt_sha256"]
        or captured.tzinfo is None
        or captured > datetime.now(timezone.utc)
        or body["model"] != "deepseek-flash"
        or body["request_extra"] != {"thinking": {"type": "disabled"}}
        or body["source_id"] != "item:" + sale.item_id
        or body["source_url"] != canonical_public_item_url(sale.source_url)
        or body["observation_sha256"] != observation["raw_row_sha256"]
    ):
        raise ValueError("price_receipt_binding_changed")
    _read_bound_images(
        sale,
        captured,
        body["gallery"]["raw_html_sha256"],
        body["images"],
        blob_reader,
        expected_gallery=body["gallery"],
    )
    result = PriceImageResult.model_validate(body["result"])
    if result.identity_derivation_contract and not result.observation_contract:
        raise ValueError("price_identity_derivation_requires_face_trace")
    if result.observation_contract:
        from card_platform.player_prices.image_observations import compose_faces

        trace_sha = body["usage"]["observation_trace_sha256"]
        content = blob_reader(trace_sha)
        if _hash(content) != trace_sha:
            raise ValueError("price_model_trace_changed")
        trace = json.loads(content)
        records = trace["observations"]
        if (
            trace["contract"] != "price-image-model-trace-v1"
            or trace["model_calls"] != 2
            or len(records) != 2
        ):
            raise ValueError("price_model_trace_incomplete")
        raw_faces = []
        for index, record in enumerate(records):
            if (
                type(record["image_index"]) is not int
                or record["image_index"] != index
                or record["image_sha256"] != body["images"][index]["sha256"]
                or record["model"] != body["model"]
                or record["request_extra"] != body["request_extra"]
            ):
                raise ValueError("price_model_trace_unbound")
            raw_faces.append(json.loads(record["raw_response"]))
        if compose_faces(
            raw_faces, identity_derivation_contract=result.identity_derivation_contract
        ).model_dump(mode="json") != result.model_dump(mode="json"):
            raise ValueError("price_model_derivation_changed")
    return sale, result


def _same_value(field, observed, expected):
    if isinstance(expected, bool):
        return isinstance(observed, bool) and observed is expected
    if field == "card_count":
        return type(observed) is int and observed == expected
    if field in {"card_number", "grade_cert_number"}:
        return (
            expected is not None
            and str(observed).strip().lstrip("#").casefold()
            == str(expected).strip().lstrip("#").casefold()
        )
    if field in {"grade_value", "serial_number", "serial_total"}:
        try:
            return Decimal(str(observed)) == Decimal(str(expected))
        except (ValueError, ArithmeticError):
            return False
    return expected is not None and text_key(observed) == text_key(expected)


def _field(result, field, expected, indices):
    rows = [
        e
        for e in result.physical_evidence
        if e.field == field and _same_value(field, e.value, expected)
    ]
    return all(any(e.image_index == index for e in rows) for index in indices)


def _contradictory_fields(result, front_index, back_index):
    optional_faces = {
        "card_number": front_index,
        "parallel_name": back_index,
        "insert_name": back_index,
        "grade_company": back_index,
        "grade_value": back_index,
        "grade_cert_number": back_index,
    }
    for row in result.physical_evidence:
        if row.field == "card_count":
            expected = result.card_counts[row.image_index]
        elif row.field in type(result).model_fields:
            expected = getattr(result, row.field)
        else:
            continue
        if row.value is None and expected is None:
            continue
        if row.field in PHYSICAL_ATTRIBUTES and expected is True:
            # Positive physical features may appear on just one face. An
            # absence/unknown on the other face cannot erase the visible stamp,
            # autograph or material, nor can one face copy the other's claim.
            same_face = [
                e.value
                for e in result.physical_evidence
                if e.field == row.field and e.image_index == row.image_index
            ]
            if row.value is not True and not any(value is True for value in same_face):
                if row.value is None or row.value is False:
                    continue
        if row.value is None and optional_faces.get(row.field) == row.image_index:
            continue
        if not _same_value(row.field, row.value, expected):
            return True
    return False


def _collector_number_proven(result, back_index):
    rows = [e for e in result.physical_evidence if e.field == "card_number"]
    token = re.escape(str(result.card_number).strip().lstrip("#"))
    bound = r"(?<![\dA-Za-z])" + token + r"(?![\dA-Za-z])"
    for row in rows:
        if row.value is None and row.image_index != back_index:
            continue
        if row.number_role != "collector_number" or not re.search(bound, row.detail, re.I):
            return False
        detail = re.sub(r"(?:不是|并非|並非|\bnot\s+)[^，。；;,.]*", "", row.detail, flags=re.I)
        role = r"(?:serial(?:\s+(?:number|stamp))?|statistics?|stats?|copyright|限编|限編|统计|統計|版权|版權)"
        if (
            non_catalog_number_role(result.card_number, [row.detail])
            or re.search(bound + r"\s*(?:/|\bof\b)\s*\d+", detail, re.I)
            or re.search(role + r"[^\d;；。\n]{0,30}" + bound, detail, re.I)
            or re.search(
                bound + r"[\s'\"“”‘’]*(?:是|为|為|is|系因|来自|來自)\s*" + role, detail, re.I
            )
            or re.search(bound + r"\s*(?:points?|rebounds?|assists?|分|篮板|助攻)\b", detail, re.I)
        ):
            return False
    return any(row.image_index == back_index for row in rows)


def _decision(reason, identity=None):
    return dict(
        status="confirmed" if identity is not None else "rejected",
        identity=identity,
        reason_codes=[reason] if reason else [],
    )


def _bound_subject_title(identity, title):
    """Disambiguate isolated nicknames only after complete physical checks."""
    if not (
        canonical_subject_name(identity.get("player_name")) == "Victor Wembanyama"
        and text_key(identity.get("brand_name")) == "panini"
        and product_key(identity.get("product_name")) == "prizm"
        and text_key(identity.get("parallel_name")) == "base"
        and not identity.get("insert_name")
    ):
        return title, []
    compact = re.sub(r"[^A-Za-z0-9\u3400-\u9fff]+", "", title)
    kind_context = re.search(r"[纹折版]|特卡|插入|平行|卡种|卡款", compact) or re.search(
        r"(?<![A-Za-z0-9])(?:version|variant|variation|parallel|insert|zebra)(?![A-Za-z0-9])",
        title,
        re.I,
    )
    if re.search(r"斑马|外星人", title) and kind_context:
        return None, []
    alias_pattern = r"(?<![A-Za-z0-9\u3400-\u9fff])(?:斑马|外星人)(?![A-Za-z0-9\u3400-\u9fff])"
    if not re.search(alias_pattern, title):
        return title, []
    if not (
        _plain_base_title(title)
        and re.search(r"(?<![A-Za-z])Victor\s+Wembanyama(?![A-Za-z])|文班亚马", title, re.I)
    ):
        return None, []
    aliases = []

    def remove(match):
        aliases.append(match[0])
        return " "

    return re.sub(alias_pattern, remove, title), aliases


def admit_receipt(observation, receipt, player_name, catalog, *, blob_reader):
    """Re-run deterministic membership on the CURRENT cloud publication."""
    try:
        sale, result = _read_bound_receipt(observation, receipt, blob_reader)
    except (ValueError, TypeError, KeyError, OSError, AttributeError, UnicodeError):
        return _decision("invalid_image_receipt")
    return _admit_bound_result(observation, receipt, sale, result, player_name, catalog)


def _admit_bound_result(observation, receipt, sale, result, player_name, catalog, *, review=None):
    """Shared gates after the caller validates immutable source evidence."""
    if result.confidence < Decimal(".88") or not (result.front_complete and result.back_complete):
        return _decision("physical_views_incomplete")
    if result.card_counts != [1, 1] or not _field(result, "card_count", 1, [0, 1]):
        return _decision("physical_card_count_unverified")
    pair = result.pair_validation.model_dump(mode="json") if result.pair_validation else {}
    first, second = pair.get("front", {}), pair.get("back", {})
    if any(face.get("multi_subject") is not False for face in (first, second)):
        return _decision("single_subject_unverified")
    front_index, back_index = 0, 1
    if first.get("role") == "back" and second.get("role") == "front":
        pair = {**pair, "front": second, "back": first}
        front_index, back_index = 1, 0
    if _contradictory_fields(result, front_index, back_index):
        return _decision("physical_fields_conflict")
    images = receipt["images"]
    paired = derive_pair_validation(
        {"pair_validation": pair, "caveats": result.caveats},
        {"front": images[front_index]["sha256"], "back": images[back_index]["sha256"]},
    )
    if paired["status"] != "consistent":
        return _decision("pair_not_verified")
    faces = (paired["front"], paired["back"])
    subject = canonical_subject_name(player_name)
    if (
        canonical_subject_name(result.player_name) != subject
        or any(
            {canonical_subject_name(name) for name in face["player_names"]} != {subject}
            for face in faces
        )
        or any(face.get("multi_subject") for face in faces)
    ):
        return _decision("image_subject_conflict")
    if (
        not result.brand_name
        or not result.product_name
        or not result.card_number
        or not result.parallel_name
    ):
        return _decision("image_identity_incomplete")
    manufacturer_from_pair = result.identity_derivation_contract == "price-pair-manufacturer-v2"
    if any(
        product_key(face.get("product_name")) != product_key(result.product_name)
        or (
            (not manufacturer_from_pair or text_key(face.get("brand_name")))
            and text_key(face.get("brand_name")) != text_key(result.brand_name)
        )
        for face in (paired["front"], paired["back"])
    ):
        return _decision("image_product_conflict")
    if not _same_value(
        "card_number", paired["back"].get("card_number"), result.card_number
    ) or not _field(result, "card_number", result.card_number, [back_index]):
        return _decision("independent_card_number_missing")
    if not _collector_number_proven(result, back_index):
        return _decision("card_number_wrong_role")
    if not _field(result, "parallel_name", result.parallel_name, [front_index]):
        return _decision("front_finish_evidence_missing")
    if result.insert_name and not _field(result, "insert_name", result.insert_name, [front_index]):
        return _decision("front_insert_evidence_missing")
    if (
        result.condition_status != "ordinary_visible"
        or not _field(result, "condition_status", result.condition_status, [0, 1])
        or condition_rejection_reasons({}, sale.title)
    ):
        return _decision("condition_not_comparable")
    for field in PHYSICAL_ATTRIBUTES:
        value = getattr(result, field)
        established = (
            (_field(result, field, True, [0]) or _field(result, field, True, [1]))
            if value is True
            else _field(result, field, value, [0, 1])
        )
        if value is None or not established:
            return _decision("physical_attribute_unverified")
    if result.is_patch and not result.is_memorabilia:
        return _decision("physical_attribute_conflict")
    if result.is_redemption:
        return _decision("redemption_requires_separate_review")
    if result.grade_status not in {"raw", "graded"} or not _field(
        result, "grade_status", result.grade_status, [0, 1]
    ):
        return _decision("grade_status_unverified")
    if result.grade_status == "raw":
        if any(
            value is not None
            for value in (result.grade_company, result.grade_value, result.grade_cert_number)
        ):
            return _decision("grade_fields_conflict")
    elif (
        not result.grade_company
        or result.grade_value is None
        or not result.grade_cert_number
        or not all(
            _field(result, field, getattr(result, field), [front_index])
            for field in ("grade_company", "grade_value", "grade_cert_number")
        )
    ):
        return _decision("grading_label_unverified")
    if result.is_numbered:
        if not (
            result.serial_number
            and result.serial_total
            and result.serial_number <= result.serial_total
        ):
            return _decision("printed_serial_unverified")
        rows = [e for e in result.physical_evidence if e.field in {"serial_number", "serial_total"}]
        stamp = (result.serial_number, result.serial_total)
        if (
            serial_non_unique_role([e.detail for e in rows], number=stamp[0], total=stamp[1])
            or not any(
                stamp in {(int(n), int(t)) for n, t in re.findall(r"(\d+)\s*/\s*(\d+)", e.detail)}
                for e in rows
            )
            or not all(
                _field(result, field, getattr(result, field), [back_index])
                or _field(result, field, getattr(result, field), [front_index])
                for field in ("serial_number", "serial_total")
            )
        ):
            return _decision("printed_serial_unverified")
    elif result.serial_number is not None or result.serial_total is not None:
        return _decision("numbering_fields_conflict")
    title = identity_title(sale.title)
    source_certs = {
        value.casefold()
        for value in re.findall(
            r"(?:证书(?:编号|号)?|證書(?:編號|號)?|评级编号|評級編號|(?<![A-Za-z])cert(?:ificate|ification)?(?![A-Za-z])(?:\s*(?:number|no\.?|#))?)\s*[:：#]?\s*([A-Za-z0-9-]*\d[A-Za-z0-9-]*)(?![\w-])",
            title,
            re.I,
        )
    }
    if source_certs and source_certs != {str(result.grade_cert_number or "").casefold()}:
        return _decision("source_grading_certificate_conflict")
    if source_finish_conflict(title, player_name, result.parallel_name):
        return _decision("source_finish_conflict")
    source_stamps = {
        (int(n), int(t))
        for n, t in re.findall(r"(?<![\dA-Za-z])(\d+)\s*/\s*(\d+)(?!\d)", title)
        if not (
            1940 <= int(n) <= 2200
            and (int(t) in {int(n), int(n) + 1, int(n) % 100, (int(n) + 1) % 100})
        )
    }
    if source_stamps and source_stamps != {(result.serial_number, result.serial_total)}:
        return _decision("source_serial_conflict")
    seasons = explicit_seasons(title)
    if len(seasons) != 1:
        return _decision("release_season_unconfirmed")
    start, end = next(iter(seasons))
    if any(
        value is not None and value != expected
        for value, expected in ((result.season_start, start), (result.season_end, end))
    ):
        return _decision("image_season_conflict")
    identity = {
        key: getattr(result, key)
        for key in (
            "player_name",
            "brand_name",
            "product_name",
            "card_number",
            "insert_name",
            "parallel_name",
            "is_autograph",
            "is_memorabilia",
            "is_patch",
            "is_redemption",
            "grade_company",
            "grade_value",
        )
    }
    identity.update(
        season_start=start,
        season_end=end,
        print_run=result.serial_total,
        grade_value=str(result.grade_value) if result.grade_value is not None else None,
    )
    match_title, subject_aliases = _bound_subject_title(identity, title)
    if match_title is None:
        return _decision("source_identity_conflict")
    confirmed = catalog.confirm_image(identity, sale.model_copy(update={"title": match_title}))
    if confirmed is None:
        return _decision("official_card_unconfirmed")
    if text_key(confirmed.get("insert_name")) != text_key(result.insert_name):
        return _decision("image_insert_conflict")
    # All original title/source gates still run. Only enumerated missing fields
    # can be resolved by the independently read, bound physical observations.
    _score, conflicts = assess_cardhobby_match(
        confirmed, sale.model_copy(update={"title": match_title})
    )
    allowed = {
        "标题未命中目标卡号，基础/原生闪卡需图像确认": "card_number",
        "标题未命中目标卡种，需图像确认": "insert_name",
        "标题未命中目标平行版本": "parallel_name",
        "标题未提及签字属性，需图像确认": "is_autograph",
        "标题未提及物料属性，需图像确认": "is_memorabilia",
    }
    if any(reason not in allowed for reason in conflicts):
        return _decision("source_identity_conflict")
    for reason in conflicts:
        field = allowed[reason]
        if not _field(
            result,
            field,
            getattr(result, field),
            [back_index if field == "card_number" else front_index],
        ):
            return _decision("missing_title_field_unresolved")
    proof = dict(
        contract=CONTRACT,
        receipt_sha256=receipt["receipt_sha256"],
        model=receipt["model"],
        observed_at=receipt["captured_at"],
        image_sha256=[image["sha256"] for image in images],
        resolved_title_conflicts=conflicts,
        resolved_subject_aliases=subject_aliases,
        physical_condition="ordinary_visible",
        serial_number=result.serial_number,
        grade_cert_number=result.grade_cert_number,
    )
    if review is not None:
        proof.update(observation_method="codex_reviewed_model", review=review)
    return _decision(None, {**confirmed, "image_evidence": proof})
