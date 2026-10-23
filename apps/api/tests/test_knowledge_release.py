from datetime import datetime, timedelta, timezone
from hashlib import sha256
import gzip
import json

from PIL import Image
import pytest


def _fixture(tmp_path):
    path = tmp_path / "originals" / "card.jpg"
    path.parent.mkdir()
    Image.new("RGB", (30, 42), "blue").save(path)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-source-capture")
    now = datetime.now(timezone.utc)
    asset = dict(
        asset_id="asset-1",
        path=str(path),
        sha256=sha256(path.read_bytes()).hexdigest(),
        source_key="topps-a",
        source_url="https://www.topps.com/education.pdf",
        image_url="https://www.topps.com/education.pdf#page=1",
        season_start=2025,
        season_end=2026,
        side="front",
        image_type="official_design",
        status="ready",
        visibility="public_reference",
        source_artifact_path=str(source),
        source_artifact_sha256=sha256(source.read_bytes()).hexdigest(),
        source_authority="official_host",
        locator="pdf:page=1;image=1",
        caption="Official design",
        captured_at=(now - timedelta(minutes=2)).isoformat(),
        reviewed_at=(now - timedelta(minutes=1)).isoformat(),
    )
    manifest = dict(
        batch_key="official-topps-a",
        sources=[dict(key="topps-a")],
        entities=[
            dict(
                entity_type="product",
                attributes=dict(
                    season_start=2025,
                    season_end=2026,
                    official_artifact={"checksum": "x"},
                    catalog_mappings=[{"alias": "Updates"}],
                ),
            )
        ],
    )
    return manifest, asset, now


def test_release_attaches_verified_source_images_without_asserting_physical_card_identity(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, asset, now = _fixture(tmp_path)
    result = attach_reference_images(manifest, [asset], tmp_path, now=now)
    attrs = result["entities"][0]["attributes"]
    image = attrs["reference_images"][0]
    assert image["sha256"] == asset["sha256"]
    assert image["asset_path"].endswith("/originals/card.jpg")
    assert image["side"] == "front"
    assert image["player"] is None and image["card_number"] is None
    assert attrs["knowledge_media_summary"]["paired_cards"] == 0
    assert attrs["catalog_mappings"] == [{"alias": "Updates"}]
    assert "reference_images" not in manifest["entities"][0]["attributes"]


def test_release_rejects_media_bytes_changed_after_review(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, asset, now = _fixture(tmp_path)
    Image.new("RGB", (30, 42), "red").save(asset["path"])
    with pytest.raises(ValueError, match="image checksum"):
        attach_reference_images(manifest, [asset], tmp_path, now=now)


def test_release_rejects_future_review_or_wrong_product_season(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, asset, now = _fixture(tmp_path)
    with pytest.raises(ValueError, match="review timestamp"):
        attach_reference_images(
            manifest,
            [asset | {"reviewed_at": (now + timedelta(days=1)).isoformat()}],
            tmp_path,
            now=now,
        )
    with pytest.raises(ValueError, match="season"):
        attach_reference_images(manifest, [asset | {"season_start": 2024}], tmp_path, now=now)


def test_release_ignores_private_unreviewed_and_other_product_assets(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, asset, now = _fixture(tmp_path)
    rows = [
        asset | {"visibility": "private"},
        asset | {"status": "candidate"},
        asset | {"source_key": "other"},
    ]
    result = attach_reference_images(manifest, rows, tmp_path, now=now)
    assert result["entities"][0]["attributes"]["reference_images"] == []


def test_release_keeps_distributor_host_distinct_from_official_host(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, asset, now = _fixture(tmp_path)
    asset["source_authority"] = "manufacturer_document_third_party_host"
    result = attach_reference_images(manifest, [asset], tmp_path, now=now)
    assert (
        result["entities"][0]["attributes"]["reference_images"][0]["source_authority"]
        == "manufacturer_document_third_party_host"
    )


def test_release_uses_isolated_batch_asset_paths_without_rewriting_previous_assets(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, asset, now = _fixture(tmp_path)
    result = attach_reference_images(
        manifest, [asset], tmp_path, now=now, asset_prefix="knowledge-three-seasons-20261022/media"
    )
    assert (
        result["entities"][0]["attributes"]["reference_images"][0]["asset_path"]
        == "knowledge-three-seasons-20261022/media/originals/card.jpg"
    )
    assert Image.open(asset["path"]).getpixel((0, 0))[2] > 200


@pytest.mark.parametrize("prefix", ["../media", "/tmp/media", "batch/../../media"])
def test_release_rejects_asset_prefix_outside_data_namespace(tmp_path, prefix):
    from card_platform.knowledge_release import attach_reference_images

    manifest, asset, now = _fixture(tmp_path)
    with pytest.raises(ValueError, match="asset prefix"):
        attach_reference_images(manifest, [asset], tmp_path, now=now, asset_prefix=prefix)


def _verified_pair(tmp_path):
    manifest, front, now = _fixture(tmp_path)
    card = {
        "id": "official:topps-a:card12",
        "attributes": {
            "source_key": "topps-a",
            "player": "Player A",
            "card_number": "12",
            "season_start": 2025,
            "season_end": 2026,
            "parallel": None,
        },
    }
    artifact = tmp_path / "checklist.json.gz"
    artifact.write_bytes(gzip.compress(json.dumps([card]).encode()))
    manifest["entities"][0]["attributes"]["official_artifact"] = {
        "checksum": sha256(artifact.read_bytes()).hexdigest()
    }
    front.update(
        image_type="verified_card_scan",
        player="Player A",
        card_number="12",
        variant=None,
        source_url="https://www.psacard.com/cert/123",
        pair_id="psa:123",
        pair_evidence={
            "record_type": "certification",
            "record_id": "123",
            "record_url": "https://www.psacard.com/cert/123",
        },
    )
    front["identity_verification"] = {
        "status": "verified",
        "card_id": card["id"],
        "artifact_path": str(artifact),
        "artifact_sha256": sha256(artifact.read_bytes()).hexdigest(),
        "source_url": front["source_url"],
        "source_sha256": front["source_artifact_sha256"],
        "source_locator": "cert123",
    }
    backpath = tmp_path / "originals/back.jpg"
    Image.new("RGB", (30, 42), "red").save(backpath)
    back = front | {
        "asset_id": "asset-back",
        "side": "back",
        "path": str(backpath),
        "sha256": sha256(backpath.read_bytes()).hexdigest(),
    }
    front["image_url"], back["image_url"] = (
        "https://cdn.test/front.jpg",
        "https://cdn.test/back.jpg",
    )
    source = tmp_path / "cert-source.json"
    source.write_text(
        json.dumps(
            {
                "cert": "123",
                "page_url": front["source_url"],
                "observed_link_bindings": [
                    {"url": a["image_url"], "orientation": a["side"]} for a in (front, back)
                ],
            }
        )
    )
    digest = sha256(source.read_bytes()).hexdigest()
    front["identity_verification"]["source_sha256"] = digest
    review = tmp_path / "identity-review.json"
    review.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "certification_number": "123",
                        "source_key": "topps-a",
                        "status": "exact_official_section_matched",
                        "chosen_card_id": card["id"],
                        "candidate_card_ids": [card["id"]],
                        "canonical_player": "Player A",
                        "card_number": "12",
                        "variant": None,
                        "source_identity": {
                            "source_page_url": front["source_url"],
                            "source_artifact_sha256": digest,
                        },
                        "image_evidence": [
                            {"side": a["side"], "sha256": a["sha256"]} for a in (front, back)
                        ],
                    }
                ]
            }
        )
    )
    for asset in (front, back):
        asset.update(
            source_artifact_path=str(source),
            source_artifact_sha256=digest,
            review_artifact_path=str(review),
            review_artifact_sha256=sha256(review.read_bytes()).hexdigest(),
        )
    return manifest, front, back, now


def test_release_exposes_card_identity_only_after_source_and_checklist_agree(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, front, back, now = _verified_pair(tmp_path)
    result = attach_reference_images(manifest, [front, back], tmp_path, now=now)
    attrs = result["entities"][0]["attributes"]
    assert attrs["reference_images"][0]["player"] == "Player A"
    assert attrs["reference_images"][0]["card_number"] == "12"
    assert attrs["reference_images"][0]["variant"] is None
    assert attrs["reference_images"][0]["pair_id"] == "psa:123"
    assert attrs["knowledge_media_summary"]["paired_cards"] == 1
    assert attrs["knowledge_media_summary"]["verified_card_images"] == 2
    assert "artifact_path" not in attrs["reference_images"][0]["identity_verification"]


def test_release_never_promotes_a_lone_face_to_a_front_back_pair(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, front, _, now = _verified_pair(tmp_path)
    result = attach_reference_images(manifest, [front], tmp_path, now=now)
    attrs = result["entities"][0]["attributes"]
    assert attrs["reference_images"][0]["pair_id"] is None
    assert attrs["knowledge_media_summary"]["paired_cards"] == 0


def test_release_rejects_photo_identity_that_disagrees_with_official_card(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, front, _, now = _verified_pair(tmp_path)
    with pytest.raises(ValueError, match="card identity"):
        attach_reference_images(manifest, [front | {"card_number": "13"}], tmp_path, now=now)


def test_release_rejects_tampered_identity_attachment(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, front, _, now = _verified_pair(tmp_path)
    front["identity_verification"]["artifact_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checklist checksum"):
        attach_reference_images(manifest, [front], tmp_path, now=now)


def test_release_rejects_an_unrelated_but_self_consistent_checklist(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, front, _, now = _verified_pair(tmp_path)
    manifest["entities"][0]["attributes"]["official_artifact"]["checksum"] = "f" * 64
    with pytest.raises(ValueError, match="checklist checksum"):
        attach_reference_images(manifest, [front], tmp_path, now=now)


def test_release_rejects_missing_core_identity_on_both_sides_of_comparison(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, front, _, now = _verified_pair(tmp_path)
    from pathlib import Path

    artifact = Path(front["identity_verification"]["artifact_path"])
    rows = json.loads(gzip.decompress(artifact.read_bytes()))
    rows[0]["attributes"]["player"] = None
    artifact.write_bytes(gzip.compress(json.dumps(rows).encode()))
    digest = sha256(artifact.read_bytes()).hexdigest()
    front.update(player=None)
    front["identity_verification"]["artifact_sha256"] = digest
    manifest["entities"][0]["attributes"]["official_artifact"]["checksum"] = digest
    with pytest.raises(ValueError, match="card identity"):
        attach_reference_images(manifest, [front], tmp_path, now=now)


def test_release_rejects_chosen_identity_outside_candidates(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, front, _, now = _verified_pair(tmp_path)
    front["identity_verification"] = front["identity_verification"] | {
        "candidate_card_ids": [front["identity_verification"]["card_id"]],
        "card_id": "invented",
    }
    with pytest.raises(ValueError, match="card identity"):
        attach_reference_images(manifest, [front], tmp_path, now=now)


def test_release_rejects_card_photo_not_bound_to_identity_review(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, front, _, now = _verified_pair(tmp_path)
    path = tmp_path / "originals" / "other.jpg"
    Image.new("RGB", (30, 42), "yellow").save(path)
    front.update(path=str(path), sha256=sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="identity review"):
        attach_reference_images(manifest, [front], tmp_path, now=now)


def test_release_rejects_verified_scan_label_without_verification(tmp_path):
    from card_platform.knowledge_release import attach_reference_images

    manifest, asset, now = _fixture(tmp_path)
    with pytest.raises(ValueError, match="card identity"):
        attach_reference_images(
            manifest, [asset | {"image_type": "verified_card_scan"}], tmp_path, now=now
        )


def _listed_pair(tmp_path):
    from pathlib import Path

    manifest, front, back, now = _verified_pair(tmp_path)
    artifact = Path(front["identity_verification"]["artifact_path"])
    cards = json.loads(gzip.decompress(artifact.read_bytes()))
    cards[0]["attributes"].update(parallel="Mojo", serial_total=25)
    artifact.write_bytes(gzip.compress(json.dumps(cards).encode()))
    checksum = sha256(artifact.read_bytes()).hexdigest()
    manifest["entities"][0]["attributes"]["official_artifact"]["checksum"] = checksum
    url = "https://www.ebay.com/itm/287531379506"
    detail = tmp_path / "serial.jpg"
    Image.new("RGB", (50, 50), "green").save(detail)
    serial = {
        "number": 21,
        "total": 25,
        "image_sha256": sha256(detail.read_bytes()).hexdigest(),
        "image_path": "serial.jpg",
        "image_url": "https://cdn.test/detail.jpg",
        "locator": "Visible 21/25 on same listing closeup",
    }
    source = {
        "record_type": "public_specimen_listing",
        "record_id": "287531379506",
        "page_url": url,
        "serial_evidence": serial,
        "observed_link_bindings": [{"url": a["image_url"]} for a in (front, back)]
        + [{"url": serial["image_url"]}],
    }
    path = Path(front["source_artifact_path"])
    path.write_text(json.dumps(source))
    digest = sha256(path.read_bytes()).hexdigest()
    review_path = Path(front["review_artifact_path"])
    review = json.loads(review_path.read_text())
    record = review["records"][0]
    record.pop("certification_number")
    record.update(specimen_listing_id="287531379506", variant="Mojo", serial_evidence=serial)
    record["source_identity"] = {"source_page_url": url, "source_artifact_sha256": digest}
    record["image_evidence"].append({"side": "detail", "sha256": serial["image_sha256"]})
    review_path.write_text(json.dumps(review))
    for a in (front, back):
        a.update(
            source_url=url,
            variant="Mojo",
            serial_number=21,
            serial_total=25,
            pair_id="listing:287531379506",
            source_artifact_sha256=digest,
            review_artifact_sha256=sha256(review_path.read_bytes()).hexdigest(),
            pair_evidence={
                "record_type": "public_specimen_listing",
                "record_id": "287531379506",
                "record_url": url,
            },
        )
        a["identity_verification"].update(
            artifact_sha256=checksum, source_url=url, source_sha256=digest
        )
    return manifest, front, back, now


def test_release_accepts_listing_pair_only_with_reviewed_unique_serial_and_official_variant(
    tmp_path,
):
    from card_platform.knowledge_release import attach_reference_images

    manifest, front, back, now = _listed_pair(tmp_path)
    attrs = attach_reference_images(manifest, [front, back], tmp_path, now=now)["entities"][0][
        "attributes"
    ]
    assert attrs["knowledge_media_summary"]["paired_cards"] == 1
    assert attrs["reference_images"][0]["pair_evidence"]["record_type"] == "public_specimen_listing"
    assert attrs["reference_images"][0]["serial_number"] == 21
    assert attrs["reference_images"][1]["serial_total"] == 25


@pytest.mark.parametrize("mutation", ["missing_serial", "conflicting_total", "wrong_listing"])
def test_release_rejects_listing_identity_without_same_serial_source_and_review(tmp_path, mutation):
    from pathlib import Path
    from card_platform.knowledge_release import attach_reference_images

    manifest, front, back, now = _listed_pair(tmp_path)
    if mutation == "conflicting_total":
        front["serial_total"] = 99
    else:
        source_path = Path(front["source_artifact_path"])
        source = json.loads(source_path.read_text())
        if mutation == "missing_serial":
            source.pop("serial_evidence")
        else:
            source["record_id"] = "other-listing"
        source_path.write_text(json.dumps(source))
        digest = sha256(source_path.read_bytes()).hexdigest()
        front["source_artifact_sha256"] = digest
        front["identity_verification"]["source_sha256"] = digest
        review_path = Path(front["review_artifact_path"])
        review = json.loads(review_path.read_text())
        review["records"][0]["source_identity"]["source_artifact_sha256"] = digest
        review_path.write_text(json.dumps(review))
        front["review_artifact_sha256"] = sha256(review_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="listing"):
        attach_reference_images(manifest, [front, back], tmp_path, now=now)


@pytest.mark.parametrize("mutation", ["deleted", "tampered", "unbound_url", "outside_path"])
def test_listing_serial_closeup_requires_real_sealed_bytes_and_same_listing_url(tmp_path, mutation):
    from pathlib import Path
    from card_platform.knowledge_release import attach_reference_images

    manifest, front, back, now = _listed_pair(tmp_path)
    if mutation == "deleted":
        (tmp_path / "serial.jpg").unlink()
    elif mutation == "tampered":
        (tmp_path / "serial.jpg").write_bytes(b"tampered closeup")
    else:
        source_path = Path(front["source_artifact_path"])
        source = json.loads(source_path.read_text())
        if mutation == "unbound_url":
            source["serial_evidence"]["image_url"] = "https://unrelated.example/other-card.jpg"
        else:
            source["serial_evidence"]["image_path"] = "../unrelated.jpg"
        source_path.write_text(json.dumps(source))
        digest = sha256(source_path.read_bytes()).hexdigest()
        front["source_artifact_sha256"] = digest
        front["identity_verification"]["source_sha256"] = digest
        review_path = Path(front["review_artifact_path"])
        review = json.loads(review_path.read_text())
        review["records"][0]["serial_evidence"] = source["serial_evidence"]
        review["records"][0]["source_identity"]["source_artifact_sha256"] = digest
        review_path.write_text(json.dumps(review))
        front["review_artifact_sha256"] = sha256(review_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="listing"):
        attach_reference_images(manifest, [front], tmp_path, now=now)
