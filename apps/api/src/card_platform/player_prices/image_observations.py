"""Compose independent image facts without copying a claim between faces."""

from decimal import Decimal
import re
from typing import Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt

from card_platform.domain.season import explicit_seasons
from card_platform.knowledge_card_catalog import product_key, text_key
from card_platform.player_prices.subjects import canonical_subject_name

OBSERVATION_CONTRACT = "price-per-face-observation-v1"
PAIR_MANUFACTURER_CONTRACT = "price-pair-manufacturer-v2"
ATTRIBUTES = {
    "autograph": "is_autograph",
    "memorabilia": "is_memorabilia",
    "patch": "is_patch",
    "redemption": "is_redemption",
    "serial": "is_numbered",
}


class Fact(BaseModel):
    value: str | None = None
    detail: str | None = Field(default=None, max_length=600)
    number_role: str | None = None


class Presence(BaseModel):
    state: Literal["present", "absent_in_view", "unreadable"] = "unreadable"
    detail: str | None = Field(default=None, max_length=600)


class Surface(BaseModel):
    rainbow: Literal["visible", "not_visible", "unreadable"] = "unreadable"
    fixed_colors: list[str] | None = None
    pattern: Literal[
        "none", "cracked_ice", "wave", "checkerboard", "striped", "other", "unreadable"
    ] = "unreadable"
    detail: str | None = Field(default=None, max_length=600)


class FinishMark(BaseModel):
    text: str
    detail: str = Field(min_length=2, max_length=600)
    kind: Literal["variant_mark", "product_logo", "copyright", "unknown"] = "unknown"


class Grade(BaseModel):
    status: Literal["raw", "graded", "unknown"] = "unknown"
    company: str | None = None
    score: Decimal | None = Field(default=None, ge=0, le=10)
    cert_number: str | None = None
    detail: str | None = Field(default=None, max_length=600)


class FaceFacts(BaseModel):
    role: Literal["front", "back", "unknown"] = "unknown"
    complete: StrictBool | None = None
    card_count: StrictInt | None = None
    view_detail: str | None = Field(default=None, max_length=600)
    player_names: list[str] = Field(default_factory=list)
    multi_subject: StrictBool | None = None
    brand_name: str | None = None
    product_name: str | None = None
    identity_evidence: list[str] = Field(default_factory=list)
    card_number: Fact = Field(default_factory=Fact)
    insert_name: str | None = None
    surface: Surface = Field(default_factory=Surface)
    finish_markings: list[FinishMark] = Field(default_factory=list)
    finish_markings_complete: StrictBool | None = None
    release_season: Fact = Field(default_factory=Fact)
    serial_stamp: Fact = Field(default_factory=Fact)
    grade: Grade = Field(default_factory=Grade)
    presence: dict[str, Presence] = Field(default_factory=dict)
    confidence: Decimal = Field(ge=0, le=1)
    caveats: list[str] = Field(default_factory=list)


def _presence(face, name):
    return face.presence.get(name, Presence())


def _boolean(face, name):
    return {"present": True, "absent_in_view": False, "unreadable": None}[
        _presence(face, name).state
    ]


def _combine_presence(faces, name):
    values = [_boolean(face, name) for face in faces]
    return (
        True
        if any(value is True for value in values)
        else False
        if all(value is False for value in values)
        else None
    )


def _product(face):
    if text_key(face.brand_name) == "topps" and product_key(face.product_name) == "chrome":
        return "Topps Chrome"
    if text_key(face.brand_name) == "panini" and product_key(face.product_name) == "prizm":
        return "Prizm"
    return face.product_name


def _known_nonvariant_mark(mark, scope):
    detail = re.sub(
        r"\bnot\s+(?:an?\s+)?(?:variant|version|parallel)\s+(?:mark|label|stamp)|(?:不是|并非|未见|无)(?:独立|额外)?(?:版本|平行)(?:标识|标签|字样)",
        "",
        mark.detail,
        flags=re.I,
    )
    if re.search(
        r"(?:variant|version|parallel)\s+(?:mark|label|stamp)|(?:独立|额外|版本|平行).{0,4}(?:标识|标签|字样)|(?:not|不是|并非)\s*(?:an?\s+)?(?:product\s*logo|copyright|产品\s*logo|版权)",
        detail,
        re.I,
    ):
        return False
    logos = {
        ("topps", "chrome"): {"topps", "chrome", "toppschrome"},
        ("panini", "prizm"): {"panini", "prizm", "prizmbasketball", "paniniprizm"},
    }.get(scope, set())
    if mark.kind == "product_logo":
        return text_key(mark.text) in logos
    if mark.kind != "copyright":
        return False
    if not re.fullmatch(r"[A-Za-z0-9\s©®™().,/&:'\-–—]+", mark.text):
        return False
    # Only familiar legal/product text is ignorable. A model's "copyright"
    # classification cannot erase RED REFRACTOR or another unrecognised label.
    words = set(re.findall(r"[a-z]+", mark.text.casefold()))
    legal = {
        "copyright",
        "all",
        "rights",
        "reserved",
        "america",
        "inc",
        "llc",
        "nba",
        "properties",
        "the",
        "national",
        "basketball",
        "players",
        "association",
    }
    product_words = {"topps", "chrome"} if scope == ("topps", "chrome") else {"panini", "prizm"}
    numbers = re.findall(r"\d+(?:\s*[-–]\s*\d+)?", mark.text)
    for number in numbers:
        year = re.fullmatch(r"((?:19|20|21)\d{2})(?:\s*[-–]\s*(\d{2}|\d{4}))?", number)
        if not year or not 1940 <= int(year[1]) <= 2200:
            return False
        if year[2] and int(year[2]) != (
            (int(year[1]) + 1) % 100 if len(year[2]) == 2 else int(year[1]) + 1
        ):
            return False
    return (
        bool(words & (product_words | {"nba", "association"}))
        and words <= legal | product_words
        and not re.search(r"\d\s*/\s*\d", mark.text)
        and ("©" in mark.text or "copyright" in words or bool(numbers))
    )


def _finish(front, back, numbered, *, scope=None):
    # These narrow recipes require affirmative surface observations and a
    # complete independently observed reverse. An absent pattern is not an
    # unreadable pattern. The current official catalog still checks core scope.
    # Topps' own description distinguishes the clean rainbow finish from
    # coloured/patterned variants: https://ripped.topps.com/definition/true-refractor/
    surface = front.surface
    if (
        front.complete is not True
        or back.complete is not True
        or numbered is not False
        or front.finish_markings_complete is not True
        or back.finish_markings_complete is not True
        or surface.fixed_colors != []
        or surface.pattern != "none"
    ):
        return None, None
    if scope is None:
        scope = (text_key(front.brand_name), product_key(front.product_name))
    version_marks = []
    for mark in [*front.finish_markings, *back.finish_markings]:
        if _known_nonvariant_mark(mark, scope):
            continue
        if mark.kind != "variant_mark":
            return None, None
        version_marks.append(mark)
        detail = re.sub(
            r"(?:separate\s+from|outside(?:\s+of)?|independent\s+of|独立于|不属于|不在)[^;；。,.\n]*",
            "",
            mark.detail,
            flags=re.I,
        )
        if re.search(
            r"copyright|product\s*(?:logo|line)|产品\s*(?:logo|行)|版权|不是.*(?:版本|平行)标识|not\s+(?:an?\s+)?(?:variant|version)",
            detail,
            re.I,
        ):
            return None, None
    back_marks = {text_key(mark.text) for mark in back.finish_markings if mark in version_marks}
    marks = {text_key(mark.text) for mark in version_marks}
    if (
        scope == ("topps", "chrome")
        and surface.rainbow == "visible"
        and back_marks == marks == {"refractor"}
    ):
        return "Refractor", "topps-chrome-smooth-rainbow-reverse-refractor-v1"
    if (
        scope == ("panini", "prizm")
        and surface.rainbow == "visible"
        and back_marks == marks == {"prizm"}
    ):
        return "Silver Prizm", "prizm-smooth-rainbow-reverse-prizm-v1"
    if (
        scope in {("topps", "chrome"), ("panini", "prizm")}
        and surface.rainbow == "not_visible"
        and not marks
    ):
        return "Base", "chrome-stock-plain-front-unmarked-reverse-v1"
    return None, None


def _season(face):
    fact = face.release_season
    if fact.number_role != "release_label" or not fact.value:
        return None, None
    seasons = explicit_seasons(fact.value)
    if len(seasons) == 1:
        return next(iter(seasons))
    if re.fullmatch(r"(?:19|20)\d{2}", fact.value.strip()):
        return int(fact.value), None
    return None, None


def compose_faces(raw_faces, *, identity_derivation_contract=None):
    """Return the established admission contract, preserving per-view originals."""
    from card_platform.player_prices.image_evidence import PriceImageResult

    if identity_derivation_contract not in {None, PAIR_MANUFACTURER_CONTRACT}:
        raise ValueError("unknown_price_identity_derivation")
    if len(raw_faces) != 2:
        raise ValueError("price_pair_images_required")
    faces = [FaceFacts.model_validate(raw) for raw in raw_faces]
    front_index = next((i for i, f in enumerate(faces) if f.role == "front"), 0)
    back_index = next((i for i, f in enumerate(faces) if f.role == "back"), 1)
    front, back = faces[front_index], faces[back_index]
    attributes = {field: _combine_presence(faces, name) for name, field in ATTRIBUTES.items()}
    if any(
        face.serial_stamp.value is not None
        and (
            face.serial_stamp.number_role != "unique_serial" or _boolean(face, "serial") is not True
        )
        for face in faces
    ):
        attributes["is_numbered"] = None
    brand = front.brand_name
    manufacturer_sources = []
    if identity_derivation_contract == PAIR_MANUFACTURER_CONTRACT:
        # A printed manufacturer can occur on only one face. Unknown stays
        # unknown in that face's facts; contradictory printed brands never merge.
        known = [(i, face) for i, face in enumerate(faces) if text_key(face.brand_name)]
        manufacturer_sources = [
            i for i, face in known if len("; ".join(face.identity_evidence)) >= 8
        ]
        brand = (
            known[0][1].brand_name
            if known
            and len({text_key(face.brand_name) for _, face in known}) == 1
            and len(manufacturer_sources) == len(known)
            else None
        )
    parallel, recipe = _finish(
        front,
        back,
        attributes["is_numbered"],
        scope=(text_key(brand), product_key(front.product_name)),
    )
    conditions = [
        {
            "present": "material_damage",
            "absent_in_view": "ordinary_visible",
            "unreadable": "uncertain",
        }[_presence(f, "major_damage").state]
        for f in faces
    ]
    condition = (
        "material_damage"
        if "material_damage" in conditions
        else "ordinary_visible"
        if conditions == ["ordinary_visible"] * 2
        else "uncertain"
    )
    grade_status = front.grade.status if front.grade.status == back.grade.status else "unknown"
    evidence = []

    def add(field, value, index, detail, **extra):
        if detail and len(detail) >= 8:
            evidence.append(
                dict(field=field, value=value, image_index=index, detail=detail, **extra)
            )

    seasons = [_season(face) for face in faces]
    season_start = next((start for start, _ in seasons if start is not None), None)
    season_end = next((end for _, end in seasons if end is not None), None)
    stamps = []
    for index, face in enumerate(faces):
        add("card_count", face.card_count, index, face.view_detail)
        add(
            "card_number",
            face.card_number.value,
            index,
            face.card_number.detail,
            number_role=face.card_number.number_role,
        )
        if face.insert_name is not None:
            add("insert_name", face.insert_name, index, "; ".join(face.identity_evidence))
        for name, field in ATTRIBUTES.items():
            add(field, _boolean(face, name), index, _presence(face, name).detail)
        add("condition_status", conditions[index], index, _presence(face, "major_damage").detail)
        add("grade_status", face.grade.status, index, face.grade.detail)
        for key, value in (
            ("grade_company", face.grade.company),
            ("grade_value", face.grade.score),
            ("grade_cert_number", face.grade.cert_number),
        ):
            add(key, str(value) if isinstance(value, Decimal) else value, index, face.grade.detail)
        for key, value in zip(("season_start", "season_end"), seasons[index]):
            if value is not None:
                add(key, value, index, face.release_season.detail)
        stamp = face.serial_stamp
        match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", stamp.value or "")
        if stamp.number_role == "unique_serial" and match:
            pair = tuple(map(int, match.groups()))
            stamps.append(pair)
            for field, value in zip(("serial_number", "serial_total"), pair):
                add(field, value, index, stamp.detail)
    if parallel:
        add("parallel_name", parallel, front_index, front.surface.detail)
    number, total = stamps[0] if stamps else (None, None)
    names = [canonical_subject_name(name) for name in front.player_names]

    def pair_face(face):
        return dict(
            role=face.role,
            player_names=[canonical_subject_name(name) for name in face.player_names],
            multi_subject=face.multi_subject,
            brand_name=face.brand_name,
            product_name=_product(face),
            insert_name=face.insert_name,
            card_number=face.card_number.value,
            evidence=face.identity_evidence,
        )

    return PriceImageResult.model_validate(
        dict(
            player_name=names[0] if len(set(names)) == 1 else None,
            brand_name=brand,
            product_name=_product(front),
            card_number=back.card_number.value,
            insert_name=front.insert_name or back.insert_name,
            parallel_name=parallel,
            season_start=season_start,
            season_end=season_end,
            **attributes,
            serial_number=number,
            serial_total=total,
            grade_status=grade_status,
            grade_company=front.grade.company,
            grade_value=front.grade.score,
            grade_cert_number=front.grade.cert_number,
            front_complete=front.role == "front" and front.complete is True,
            back_complete=back.role == "back" and back.complete is True,
            card_counts=[face.card_count for face in faces],
            condition_status=condition,
            confidence=min(face.confidence for face in faces),
            caveats=[c for face in faces for c in face.caveats],
            pair_validation=dict(
                status="consistent" if {f.role for f in faces} == {"front", "back"} else "unknown",
                front=pair_face(faces[0]),
                back=pair_face(faces[1]),
            ),
            physical_evidence=evidence,
            observation_contract=OBSERVATION_CONTRACT,
            identity_derivation_contract=identity_derivation_contract,
            face_observations=[face.model_dump(mode="json") for face in faces],
            derivations=(
                [
                    dict(
                        field="brand_name",
                        rule=PAIR_MANUFACTURER_CONTRACT,
                        source_indices=manufacturer_sources,
                    )
                ]
                if identity_derivation_contract and brand
                else []
            )
            + (
                [
                    dict(
                        field="parallel_name",
                        rule=recipe,
                        front_index=front_index,
                        back_index=back_index,
                    )
                ]
                if recipe
                else []
            ),
        )
    )
