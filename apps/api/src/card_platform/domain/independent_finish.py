"""Second-pass front evidence, bound to the full primary asset/identity context."""

from decimal import Decimal
import re
from typing import Literal

from pydantic import BaseModel, Field

INDEPENDENT_FINISH_CONTRACT = "independent-front-finish-v2"


class PrintedTextField(BaseModel):
    """A public printed identity field read from this front, never a private code."""

    status: Literal["readable", "not_printed", "unclear"]
    value: str | None
    location: str
    source: Literal["images"]


class TextOnlyArtworkObservation(BaseModel):
    contract: Literal["text-only-print-v1"]
    document_kind: Literal["redemption_notice"]
    whole_front_visible: bool
    no_figurative_artwork: bool
    printed_graphics: str
    object_name: PrintedTextField
    product: PrintedTextField
    card_type: PrintedTextField
    card_code: PrintedTextField = Field(description="Public printed set/card identifier; NEVER transcribe the private redeemable code.")
    year: PrintedTextField
    expiry_date: PrintedTextField
    parallel_and_serial: PrintedTextField


def _specific_text(value: str | None) -> bool:
    return bool(value and value.strip() and value.strip().casefold() not in {
        "same", "matching", "identical", "unknown", "none", "n/a",
        "一致", "相同", "同款", "匹配", "一样", "未知", "同上", "同目标",
    } and not re.fullmatch(
        r"(?:same as|identical to|matches) (?:the )?(?:target|candidate)[.!]?",
        value.strip(), re.I,
    ))


def _matching_text_only(target, candidate) -> bool:
    observations = (target, candidate)
    if any(view.artwork_kind != "text_only" or view.artwork_subjects
           or view.text_only_artwork is None for view in observations):
        return False
    pair = [view.text_only_artwork for view in observations]
    if any(not text.whole_front_visible or not text.no_figurative_artwork
           or not _specific_text(text.printed_graphics) for text in pair):
        return False
    for name in ("object_name", "product", "card_type", "card_code", "year",
                 "expiry_date", "parallel_and_serial"):
        fields = [getattr(text, name) for text in pair]
        if any(not _specific_text(field.location) or re.search(
            r"背面|卡背|反面|\bback(?:side)?\b", field.location, re.I,
        ) for field in fields):
            return False
        if name == "parallel_and_serial" and all(
            field.status == "not_printed" and field.value is None for field in fields
        ):
            continue
        if any(field.status != "readable" or not _specific_text(field.value) for field in fields):
            return False
        # Conservative transcription equality: no alias inference, OCR repair,
        # punctuation removal, date guessing or title-derived completion.
        if " ".join(fields[0].value.split()).casefold() != " ".join(fields[1].value.split()).casefold():
            return False
    return True


class ArtworkSubjectObservation(BaseModel):
    position: str = Field(description="Subject position on the printed card, in reading order.")
    visible: bool
    body_pose: str = Field(description="Specific torso, head and leg orientation of this printed subject.")
    arms_hands_pose: str = Field(description="Visible arm bends and hand positions; explicitly describe absence for non-human artwork.")
    held_object_position: str = Field(description="Ball/object position relative to this subject's body; explicitly state if none is visible.")
    distinct_image_details: str = Field(description="Specific photograph/illustration details, not names, card template, frame or finish.")


class FrontFinishObservation(BaseModel):
    layout: str
    outer_frame_fixed_color: str
    fixed_surface_pattern: str
    pattern_location: str
    lighting_or_reflection: str
    visible: bool
    view: Literal["front", "unknown"]
    artwork_kind: Literal["figurative", "text_only"] = "figurative"
    text_only_artwork: TextOnlyArtworkObservation | None = None
    artwork_subjects: list[ArtworkSubjectObservation] = Field(
        description="Every main printed subject separately in reading order. Empty if not readable; never infer poses from product or player identity."
    )


class IndependentFinishObservation(BaseModel):
    candidate: FrontFinishObservation
    target: FrontFinishObservation
    layout_match: Literal["same", "different", "unknown"]
    fixed_color_match: Literal["same", "different", "unknown"]
    texture_match: Literal["same", "different", "unknown"]
    artwork_match: Literal["same", "different", "unknown"] = Field(
        description="All corresponding printed subjects must use the same photograph/illustration, pose, hands and ball/object position. A common template is insufficient."
    )
    verdict: Literal["same", "different", "uncertain"]
    confidence: Decimal = Field(ge=0, le=1)
    reason: str

    def establishes_same(self) -> bool:
        generic = {
            "same",
            "matching",
            "identical",
            "unknown",
            "一致",
            "相同",
            "同款",
            "匹配",
            "一样",
            "未知",
        }
        return (
            self.verdict == "same"
            and self.confidence >= Decimal(".90")
            and self.layout_match == self.fixed_color_match == self.texture_match == "same"
            and self.artwork_match == "same"
            and (_matching_text_only(self.target, self.candidate) or (
                self.target.artwork_kind == self.candidate.artwork_kind == "figurative"
                and self.target.text_only_artwork is None and self.candidate.text_only_artwork is None
                and bool(self.target.artwork_subjects)
                and len(self.target.artwork_subjects) == len(self.candidate.artwork_subjects)
                and all(
                    target.position.strip().casefold() == candidate.position.strip().casefold()
                    for target, candidate in zip(self.target.artwork_subjects, self.candidate.artwork_subjects)
                )
                and all(
                    subject.visible
                    and all(
                        value.strip()
                        and value.strip().casefold() not in generic
                        and not re.fullmatch(
                            r"(?:same as|identical to|matches) (?:the )?(?:target|candidate)[.!]?",
                            value.strip(), re.I,
                        )
                        for value in (
                            subject.position, subject.body_pose, subject.arms_hands_pose,
                            subject.held_object_position, subject.distinct_image_details,
                        )
                    )
                    for observation in (self.candidate, self.target)
                    for subject in observation.artwork_subjects
                )
            ))
            and all(
                observation.visible
                and observation.view == "front"
                and all(
                    value.strip()
                    and value.strip().casefold() not in generic
                    and not re.search(r"背面|卡背|反面|\bback(?:side)?\b", value, re.I)
                    and not re.fullmatch(
                        r"(?:same as|identical to|matches) (?:the )?(?:target|candidate)[.!]?",
                        value.strip(),
                        re.I,
                    )
                    for value in (
                        observation.layout,
                        observation.outer_frame_fixed_color,
                        observation.fixed_surface_pattern,
                        observation.pattern_location,
                    )
                )
                for observation in (self.candidate, self.target)
            )
        )


def requires_text_only_reobservation(confirmation) -> bool:
    """Retry only old honest empty-subject outputs; never promote their verdict."""
    if not isinstance(confirmation, dict) or confirmation.get('contract') != INDEPENDENT_FINISH_CONTRACT:
        return False
    try:
        observation = IndependentFinishObservation.model_validate(confirmation.get('observation'))
    except (TypeError, ValueError):
        return False
    return (observation.verdict == 'same'
            and observation.layout_match == observation.fixed_color_match == observation.texture_match
            == observation.artwork_match == 'same'
            and all(not view.artwork_subjects and view.text_only_artwork is None
                    for view in (observation.target, observation.candidate)))


def independent_finish_is_current(confirmation, binding) -> bool:
    """Never promote a free-text reason or a confirmation for different assets."""
    if not isinstance(confirmation, dict) or not isinstance(binding, dict) or not binding:
        return False
    if (
        confirmation.get("contract") != INDEPENDENT_FINISH_CONTRACT
        or not str(confirmation.get("model") or "").strip()
        or confirmation.get("evidence_binding") != binding
    ):
        return False
    try:
        return IndependentFinishObservation.model_validate(
            confirmation.get("observation")
        ).establishes_same()
    except (TypeError, ValueError):
        return False
