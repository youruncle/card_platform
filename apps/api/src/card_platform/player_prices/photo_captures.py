"""Archived public photos without a model call: exclusion evidence only."""

from datetime import datetime
import json
import re

from card_platform.player_prices.image_evidence import (
    _hash,
    _seal,
    _read_bound_images,
    image_pair_key,
)
from card_platform.public_source_catalog import validated_sale


def read_capture(manifest_sha256, blob_reader):
    if not isinstance(manifest_sha256, str) or not re.fullmatch("[a-f0-9]{64}", manifest_sha256):
        raise ValueError("price_photo_manifest_invalid")
    content = blob_reader(manifest_sha256)
    if _hash(content) != manifest_sha256:
        raise ValueError("price_photo_manifest_changed")
    manifest = json.loads(content)
    observation = manifest["observation"]
    sale, _ = validated_sale(observation)
    pair = image_pair_key(manifest)
    if (
        manifest["contract"] != "price-public-pair-capture-only-v1"
        or manifest["status"] != "captured_pair"
        or manifest["source_id"] != "item:" + sale.item_id
        or pair is None
    ):
        raise ValueError("price_photo_manifest_unbound")
    captured = datetime.fromisoformat(manifest["captured_at"])
    _read_bound_images(
        sale, captured, manifest["gallery_html_sha256"], manifest["images"], blob_reader
    )
    return dict(
        id="photo:" + manifest_sha256,
        capture_manifest_sha256=manifest_sha256,
        source_id=manifest["source_id"],
        observation=observation,
        observation_sha256=observation["raw_row_sha256"],
        captured_at=captured,
        pair_a=pair[0],
        pair_b=pair[1],
        receipt=None,
        receipt_sha256=None,
        approved_review_sha256s=[],
        price_eligible=False,
    )


def read_bound_capture(capture, blob_reader):
    bound = read_capture(capture["capture_manifest_sha256"], blob_reader)
    if any(
        bound[key] != capture[key]
        for key in (
            "source_id",
            "observation_sha256",
            "capture_manifest_sha256",
            "pair_a",
            "pair_b",
        )
    ) or _seal(bound["observation"]) != _seal(capture["observation"]):
        raise ValueError("price_photo_index_binding_changed")
    return bound["pair_a"], bound["pair_b"]


def capture_pair_key(capture):
    if capture.get("capture_manifest_sha256"):
        return capture["pair_a"], capture["pair_b"]
    return image_pair_key(capture.get("receipt"))
