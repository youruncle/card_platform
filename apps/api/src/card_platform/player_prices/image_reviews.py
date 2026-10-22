"""Server-approved visual corrections, distinct from the original model output.

The approval set must come from a trusted server workflow/database, never from
the supplied review or a public client. A content hash proves integrity, not
reviewer authority. This module does not expose a client approval endpoint.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from card_platform.player_prices.image_evidence import (
    _admit_bound_result,
    _decision,
    _read_bound_receipt,
    _seal,
)
from card_platform.player_prices.image_observations import OBSERVATION_CONTRACT, compose_faces


class ReviewedImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_index: Literal[0, 1]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    detail: str = Field(min_length=16, max_length=1200)


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_index: Literal[0, 1]
    field: Literal[
        "role",
        "complete",
        "card_count",
        "view_detail",
        "player_names",
        "multi_subject",
        "brand_name",
        "product_name",
        "identity_evidence",
        "card_number",
        "insert_name",
        "surface",
        "finish_markings",
        "finish_markings_complete",
        "release_season",
        "serial_stamp",
        "grade",
        "presence",
    ]
    before: Any
    after: Any
    detail: str = Field(min_length=16, max_length=1200)


class ImageReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: Literal["price-image-review-v1"]
    reviewer_kind: Literal["codex_visual_review"]
    reviewed_at: datetime
    based_on_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewed_images: list[ReviewedImage] = Field(min_length=2, max_length=2)
    corrections: list[Correction] = Field(min_length=1, max_length=36)


def admit_review(
    observation,
    receipt,
    player_name,
    catalog,
    *,
    review_sha256,
    approved_review_sha256s,
    blob_reader,
):
    """Apply an independently approved audit record; run every admission gate."""
    try:
        if review_sha256 not in approved_review_sha256s:
            raise ValueError("unapproved_price_image_review")
        content = blob_reader(review_sha256)
        if hashlib.sha256(content).hexdigest() != review_sha256:
            raise ValueError("price_review_bytes_changed")
        review = ImageReview.model_validate(json.loads(content))
        # Always validate the actual original model trace and bound photographs
        # before a correction. Never manufacture a replacement model response.
        sale, original = _read_bound_receipt(observation, receipt, blob_reader)
        if (
            original.observation_contract != OBSERVATION_CONTRACT
            or review.based_on_receipt_sha256 != receipt["receipt_sha256"]
            or review.observation_sha256 != observation["raw_row_sha256"]
            or review.reviewed_at.tzinfo is None
            or not datetime.fromisoformat(receipt["captured_at"])
            <= review.reviewed_at
            <= datetime.now(timezone.utc)
            or {im.image_index for im in review.reviewed_images} != {0, 1}
            or any(
                im.sha256 != receipt["images"][im.image_index]["sha256"]
                for im in review.reviewed_images
            )
        ):
            raise ValueError("unbound_price_image_review")
        faces = deepcopy(original.face_observations)
        changed = set()
        for correction in review.corrections:
            key = (correction.image_index, correction.field)
            if (
                key in changed
                or faces[correction.image_index][correction.field] != correction.before
                or correction.before == correction.after
            ):
                raise ValueError("price_review_previous_value_changed")
            faces[correction.image_index][correction.field] = deepcopy(correction.after)
            changed.add(key)
        result = compose_faces(faces)
        proof = dict(
            review_sha256=review_sha256,
            reviewer_kind=review.reviewer_kind,
            reviewed_at=review.reviewed_at.isoformat(),
            correction_count=len(changed),
            original_result_fingerprint=_seal(original.model_dump(mode="json")),
            corrected_result_fingerprint=_seal(result.model_dump(mode="json")),
        )
    except (ValueError, TypeError, KeyError, OSError, AttributeError, UnicodeError, IndexError):
        return _decision("invalid_image_review")
    return _admit_bound_result(
        observation,
        receipt,
        sale,
        result,
        player_name,
        catalog,
        review=proof,
    )
