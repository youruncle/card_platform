"""Published catalogue indexes facts without promoting samples to card identities."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import gzip
from hashlib import sha256
import json

import pytest

from card_platform.knowledge_browser import KnowledgeBrowser


def fact(card_id="card:1", **attrs):
    return {
        "id": card_id,
        "entity_type": "card",
        "canonical_name": "#1 Player A",
        "attributes": {
            "manufacturer": "Topps",
            "brand": "Bowman",
            "product": "Chrome",
            "season_start": 2025,
            "season_end": 2026,
            "player": "Player A",
            "card_number": "1",
            "section": "Base",
            "parallel": "Base",
            "source_key": "source",
            "source_url": "https://example.com/list",
            **attrs,
        },
    }


def artifact(tmp_path, cards, **changes):
    blob = gzip.compress(json.dumps(cards).encode(), mtime=0)
    digest = sha256(blob).hexdigest()
    root = tmp_path / "artifacts"
    root.mkdir(exist_ok=True)
    path = root / (digest + ".json.gz")
    path.write_bytes(blob)
    return {
        "path": path.name,
        "checksum": digest,
        "card_count": len(cards),
        "source_key": "source",
        "manufacturer": "Topps",
        "brand": "Bowman",
        "product": "Chrome",
        "season_start": 2025,
        "season_end": 2026,
        **changes,
    }


def snapshot():
    return {
        "version": 7,
        "entities": [
            {"id": "topps", "entity_type": "manufacturer", "canonical_name": "Topps"},
            {"id": "leaf", "entity_type": "manufacturer", "canonical_name": "Leaf"},
            {
                "id": "bowman",
                "entity_type": "brand",
                "canonical_name": "Bowman",
                "parent_id": "topps",
            },
            {
                "id": "product",
                "entity_type": "product",
                "canonical_name": "2025-26 Bowman Chrome",
                "parent_id": "bowman",
                "aliases": ["铂金"],
                "attributes": {
                    "product": "Chrome",
                    "brand": "Bowman",
                    "season_start": 2025,
                    "season_end": 2026,
                    "source_url": "https://example.com/list",
                },
            },
            {
                "id": "generic",
                "entity_type": "product",
                "canonical_name": "Generic",
                "attributes": {},
            },
            {
                "id": "player",
                "entity_type": "player",
                "canonical_name": "Player A",
                "aliases": ["球员甲"],
            },
            fact(),
        ],
    }


def browser(tmp_path, data=None):
    return KnowledgeBrowser(
        data or snapshot(),
        version_id="version/7",
        artifact_root=tmp_path / "artifacts",
        data_root=tmp_path,
        cache_root=tmp_path / "cache",
    )


def add_image(
    tmp_path, data, name, *, kind="official_design", card_id=None, side="unknown", **extra
):
    path = tmp_path / "batch" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(name.encode())
    ref = {
        "asset_id": name,
        "asset_path": f"batch/{name}",
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "image_type": kind,
        "side": side,
        "source_url": "https://example.com/image",
        **extra,
    }
    if card_id:
        ref["identity_verification"] = {"status": "verified", "card_id": card_id}
    data["entities"][3]["attributes"].setdefault("reference_images", []).append(ref)
    return ref


def test_products_keep_generic_and_manufacturers_without_products(tmp_path):
    index = browser(tmp_path)
    summary = index.summary()
    assert summary["summary"] == {
        "manufacturers": 2,
        "products": 2,
        "card_facts": 1,
        "images": 0,
        "paired_cards": 0,
    }
    assert {r["name"]: r["product_count"] for r in summary["manufacturers"]} == {
        "Leaf": 0,
        "Topps": 1,
    }
    assert summary["discovery_complete"] is False
    assert summary["seasons"] == [2025]
    assert index.products(q="铂金")["items"][0]["manufacturer"] == "Topps"
    assert index.products(q="Generic")["items"][0]["season_start"] is None
    assert index.cards(q="球员甲")["items"][0]["product_id"] == "product"
    assert index.cards(q="铂金")["total"] == 1
    assert index.product("product")["sections"] == [{"name": "Base", "card_count": 1}]
    assert index.entities(entity_type="player", q="球员甲")["total"] == 1


def test_valid_artifact_deduplicates_entity_facts_and_excludes_invalid_sources(tmp_path):
    data = snapshot()
    ref = artifact(tmp_path, [fact(), fact("card:2", parallel="Gold", serial_total=50)])
    data["official_artifacts"] = [ref, ref | {"path": "missing.json.gz", "source_key": "missing"}]
    data["entities"][3]["attributes"]["official_artifact"] = ref
    index = browser(tmp_path, data)
    assert index.summary()["summary"]["card_facts"] == 2
    assert (
        index.products()["items"][0]["card_count"] + index.products()["items"][1]["card_count"] == 2
    )
    assert index.cards(parallel="Gold")["items"][0]["serial_total"] == 50
    assert index.card("card:1")["related_versions"][0]["id"] == "card:2"
    assert index.summary()["issues"][0]["source_key"] == "missing"
    assert str(tmp_path) not in json.dumps(index.summary())


@pytest.mark.parametrize(
    "change",
    [{"card_count": 4}, {"checksum": "0" * 64}, {"product": "Other"}, {"season_start": 2024}],
)
def test_invalid_artifact_is_quarantined_atomically(tmp_path, change):
    data = snapshot()
    data["official_artifacts"] = [artifact(tmp_path, [fact("new")], **change)]
    index = browser(tmp_path, data)
    assert index.cards()["total"] == 1
    assert len(index.summary()["issues"]) == 1


def test_artifact_missing_after_cache_build_is_not_hidden(tmp_path):
    data = snapshot()
    ref = artifact(tmp_path, [fact("new")])
    data["official_artifacts"] = [ref]
    index = browser(tmp_path, data)
    assert index.cards()["total"] == 2
    (tmp_path / "artifacts" / ref["path"]).unlink()
    assert index.cards()["total"] == 1
    assert index.summary()["issues"]
    assert browser(tmp_path, data).cards()["total"] == 1


def test_media_has_exact_binding_unknown_sides_and_real_pairs_only(tmp_path):
    data = snapshot()
    add_image(
        tmp_path,
        data,
        "design.jpg",
        side="front",
        player="Player A",
        card_number="1",
        pair_id="fake",
    )
    add_image(
        tmp_path,
        data,
        "front.jpg",
        kind="verified_card_scan",
        card_id="card:1",
        side="front",
        pair_id="pair",
    )
    add_image(
        tmp_path,
        data,
        "back.jpg",
        kind="verified_card_scan",
        card_id="card:1",
        side="back",
        pair_id="pair",
    )
    add_image(tmp_path, data, "unknown.jpg", kind="verified_card_scan", card_id="card:1")
    add_image(
        tmp_path,
        data,
        "ambiguous.jpg",
        kind="verified_card_scan",
        identity_verification={"status": "verified", "candidate_card_ids": ["card:1", "other"]},
    )
    index = browser(tmp_path, data)
    design = index.media(kind="design")["items"][0]
    assert design["card_id"] is None and design["pair_id"] is None
    images = index.card("card:1")["images"]
    assert len(images) == 3
    assert {i["side"] for i in images} == {"front", "back", "unknown"}
    assert index.summary()["summary"]["paired_cards"] == 1
    assert index.cards(image_state="has_image")["total"] == 1
    assert index.cards(image_state="missing_back")["total"] == 0
    assert "version_id=version%2F7" in design["image_url"]
    path, digest = index.media_file(design["id"])
    assert path == tmp_path / "batch/design.jpg"
    assert digest == sha256(b"design.jpg").hexdigest()


def test_private_unreviewed_unknown_type_and_escaping_media_are_excluded(tmp_path):
    data = snapshot()
    for name, extra in [
        ("private.jpg", {"visibility": "private"}),
        ("draft.jpg", {"review_status": "pending"}),
        ("other.jpg", {"image_type": "user_upload"}),
        ("escape.jpg", {"asset_path": "../escape.jpg"}),
    ]:
        add_image(tmp_path, data, name, **extra)
    index = browser(tmp_path, data)
    assert index.media()["total"] == 0
    with pytest.raises(KeyError):
        index.media_file("../escape.jpg")


def test_scope_does_not_merge_same_product_brand_or_season(tmp_path):
    data = snapshot()
    other = deepcopy(data["entities"][3])
    other.update(id="other", parent_id=None)
    other["attributes"].update(manufacturer="Panini", brand="Donruss")
    data["entities"].extend(
        [
            other,
            fact("foreign", manufacturer="Panini", brand="Donruss"),
            fact("year", season_start=2024, season_end=2025),
        ]
    )
    index = browser(tmp_path, data)
    assert index.cards(product_id="product")["total"] == 1
    assert index.cards(product_id="other")["items"][0]["id"] == "foreign"
    assert index.card("card:1")["related_versions"] == []
    assert index.card("year")["card"]["product_id"] is None


def test_related_versions_require_complete_exact_section_identity_and_cap(tmp_path):
    data = snapshot()
    data["entities"].extend([fact(f"v{i}", parallel=f"P{i}") for i in range(40)])
    data["entities"].extend(
        [
            fact("other-section", section="Rookies"),
            fact("other-player", player="Other"),
            fact("unknown-section", section=None),
        ]
    )
    index = browser(tmp_path, data)
    related = index.card("card:1")["related_versions"]
    assert len(related) == 30
    assert all(r["section"] == "Base" and r["player"] == "Player A" for r in related)
    assert index.card("unknown-section")["related_versions"] == []


def test_parallel_readers_and_builders_preserve_snapshot_and_sources(tmp_path):
    data = snapshot()
    ref = artifact(tmp_path, [fact("new")])
    data["official_artifacts"] = [ref]
    before = deepcopy(data)
    blob = (tmp_path / "artifacts" / ref["path"]).read_bytes()
    with ThreadPoolExecutor(max_workers=6) as pool:
        indexes = list(pool.map(lambda _: browser(tmp_path, data), range(6)))
        result = list(pool.map(lambda i: indexes[i % 6].cards()["total"], range(12)))
    assert result == [2] * 12
    assert data == before
    assert (tmp_path / "artifacts" / ref["path"]).read_bytes() == blob


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("cards", {"limit": 0}),
        ("products", {"limit": 101}),
        ("entities", {"offset": -1}),
        ("media", {"kind": "bogus"}),
        ("cards", {"image_state": "bogus"}),
    ],
)
def test_invalid_queries_are_rejected(tmp_path, method, kwargs):
    with pytest.raises(ValueError):
        getattr(browser(tmp_path), method)(**kwargs)


def test_generic_alias_enriches_exact_family_without_optic_bleed(tmp_path):
    data = snapshot()
    generic = {
        "id": "generic-family",
        "entity_type": "product",
        "canonical_name": "National Treasures",
        "aliases": ["国宝"],
    }
    annual = {
        "id": "annual-family",
        "entity_type": "product",
        "canonical_name": "2025 Panini National Treasures",
        "attributes": {
            "product": "Panini National Treasures",
            "manufacturer": "Panini",
            "season_start": 2025,
            "season_end": 2026,
        },
    }
    optic = deepcopy(annual)
    optic.update(id="optic-family", canonical_name="2025 Panini National Treasures Optic")
    optic["attributes"]["product"] = "Panini National Treasures Optic"
    data["entities"].extend(
        [
            generic,
            annual,
            optic,
            fact(
                "treasure",
                manufacturer="Panini",
                brand="Panini",
                product="Panini National Treasures",
            ),
        ]
    )
    index = browser(tmp_path, data)
    assert {p["id"] for p in index.products(q="国宝")["items"]} == {
        "generic-family",
        "annual-family",
    }
    assert index.cards(q="国宝")["items"][0]["id"] == "treasure"


def test_manufacturer_brand_fallback_keeps_historical_maker_and_excludes_bowman(tmp_path):
    data = {
        "entities": [
            {
                "id": "sky",
                "entity_type": "product",
                "canonical_name": "Old SkyBox",
                "attributes": {"brand": "SkyBox"},
            },
            {
                "id": "bow",
                "entity_type": "product",
                "canonical_name": "Bowman",
                "attributes": {"brand": "Bowman"},
            },
        ]
    }
    index = browser(tmp_path, data)
    assert index.summary()["manufacturers"] == [
        {"name": "SkyBox", "product_count": 1, "card_count": 0, "image_count": 0}
    ]
    assert index.product("bow")["product"]["manufacturer"] is None


def test_summary_card_type_counts_include_immutable_attachments(tmp_path):
    data = snapshot()
    data["official_artifacts"] = [artifact(tmp_path, [fact("attachment")])]
    index = browser(tmp_path, data)
    assert {r["type"]: r["count"] for r in index.summary()["entity_types"]}["card"] == 2


def test_unknown_optional_shapes_do_not_crash_published_catalogue(tmp_path):
    data = snapshot()
    data["entities"][3]["aliases"] = None
    data["entities"][3]["attributes"]["text_evidence"] = "unknown"
    add_image(
        tmp_path,
        data,
        "unknown-proof.jpg",
        kind="verified_card_scan",
        identity_verification=["unresolved"],
    )
    assert browser(tmp_path, data).cards()["total"] == 1


def test_unapproved_entities_and_aliases_do_not_enter_browser(tmp_path):
    data = snapshot()
    data["entities"].append(fact("draft", review_status="pending"))
    data["entities"][-1]["review_status"] = "pending"
    data["entities"].append(
        {
            "id": "draft-product",
            "entity_type": "product",
            "canonical_name": "Draft",
            "review_status": "pending",
        }
    )
    index = browser(tmp_path, data)
    assert index.cards()["total"] == 1
    assert index.products(q="Draft")["total"] == 0


def test_card_sources_preserve_structured_and_explicit_locators_and_authority(tmp_path):
    data = snapshot()
    data["entities"][-1]["attributes"].update(
        source_locator="pdf:page=4;line=8",
        source_locators=[{"source_page": 4, "source_line": 8, "raw_text": "Player A"}],
        authority="official_host",
    )
    sources = browser(tmp_path, data).card("card:1")["card"]["sources"]
    assert sources == [
        {
            "url": "https://example.com/list",
            "locator": "pdf:page=4;line=8",
            "authority": "official_host",
        }
    ]
    data["entities"][-1]["attributes"].pop("source_locator")
    assert (
        browser(tmp_path, data).card("card:1")["card"]["sources"][0]["locator"] == "page 4, line 8"
    )


def test_card_owned_reference_is_explicit_even_when_side_unknown(tmp_path):
    data = snapshot()
    ref = add_image(tmp_path, data, "card-owned.jpg", kind="verified_card_scan")
    data["entities"][3]["attributes"]["reference_images"] = []
    data["entities"][-1]["attributes"]["reference_images"] = [ref]
    index = browser(tmp_path, data)
    assert index.cards(image_state="has_image")["total"] == 1
    assert index.card("card:1")["images"][0]["side"] == "unknown"
    assert index.card("card:1")["images"][0]["card_id"] == "card:1"
    assert index.summary()["summary"]["paired_cards"] == 0


def test_initial_missing_corrupt_assets_do_not_count_as_viewable_coverage(tmp_path):
    data = snapshot()
    add_image(tmp_path, data, "missing.jpg")
    add_image(tmp_path, data, "corrupt.jpg")
    (tmp_path / "batch/missing.jpg").unlink()
    (tmp_path / "batch/corrupt.jpg").write_bytes(b"changed")
    index = browser(tmp_path, data)
    assert index.summary()["summary"]["images"] == 0
    assert len(index.summary()["issues"]) == 2
    assert index.products(has_images=True)["total"] == 0


def test_later_asset_changes_remain_addressable_for_api_checksum_error(tmp_path):
    data = snapshot()
    add_image(tmp_path, data, "later.jpg")
    index = browser(tmp_path, data)
    media_id = index.media()["items"][0]["id"]
    path, digest = index.media_file(media_id)
    path.write_bytes(b"changed")
    assert index.media_file(media_id) == (path, digest)
    path.unlink()
    assert index.media_file(media_id) == (path, digest)


def test_duplicate_product_scope_uses_published_source_identity(tmp_path):
    data = snapshot()
    original = data["entities"][3]
    original["attributes"]["source_key"] = "source"
    duplicate = deepcopy(original)
    duplicate["id"] = "duplicate"
    duplicate["attributes"].update(source_key="other", source_url="https://other.example.com/list")
    data["entities"].append(duplicate)
    index = browser(tmp_path, data)
    assert index.card("card:1")["card"]["product_id"] == "product"


@pytest.mark.parametrize("image_type", ["verified_card", "verified_specimen"])
def test_other_published_verified_image_labels_require_explicit_binding(tmp_path, image_type):
    data = snapshot()
    add_image(tmp_path, data, "published.jpg", kind=image_type, card_id="card:1")
    index = browser(tmp_path, data)
    assert index.media(kind="specimen")["total"] == 1
    assert index.cards(image_state="has_image")["total"] == 1


def test_conflicting_owner_and_identity_do_not_bind_an_image(tmp_path):
    data = snapshot()
    data["entities"].append(fact("other"))
    ref = add_image(tmp_path, data, "conflict.jpg", kind="verified_card_scan", card_id="other")
    data["entities"][3]["attributes"]["reference_images"] = []
    data["entities"][6]["attributes"]["reference_images"] = [ref]
    index = browser(tmp_path, data)
    assert index.cards(image_state="has_image")["total"] == 0


def test_orphan_card_media_does_not_poison_products_without_images_filter(tmp_path):
    data = snapshot()
    data["entities"][-1]["attributes"]["product"] = "Unknown release"
    ref = add_image(tmp_path, data, "orphan.jpg", kind="verified_card_scan")
    data["entities"][3]["attributes"]["reference_images"] = []
    data["entities"][-1]["attributes"]["reference_images"] = [ref]
    index = browser(tmp_path, data)
    assert index.products(has_images=False)["total"] == 2
    assert index.products(has_images=True)["total"] == 0
    assert (
        next(r for r in index.summary()["manufacturers"] if r["name"] == "Topps")["image_count"]
        == 1
    )


def test_opaque_product_ids_are_case_sensitive_and_search_pages_are_stable(tmp_path):
    data = snapshot()
    data["entities"].extend([fact("aaa", parallel="Gold"), fact("zzz", parallel="Gold")])
    index = browser(tmp_path, data)
    assert index.cards(product_id="PRODUCT")["total"] == 0
    first = index.cards(q="球员甲", limit=1, offset=1)
    assert first["total"] == 3
    assert first["items"][0]["id"] == "aaa"
    assert [index.cards(q="球员甲", limit=1, offset=i)["items"][0]["id"] for i in range(3)] == [
        "card:1",
        "aaa",
        "zzz",
    ]
    assert index.cards(product_id="product", q="球员甲", parallel="Gold")["total"] == 2
    assert (
        index.cards(manufacturer="topps", q="球员甲", parallel="Gold", limit=1, offset=1)["items"][
            0
        ]["id"]
        == "zzz"
    )


def test_media_responses_include_published_product_context(tmp_path):
    data = snapshot()
    add_image(tmp_path, data, "context.jpg", kind="verified_card_scan", card_id="card:1")
    index = browser(tmp_path, data)
    media = index.media()["items"][0]
    assert {
        k: media[k] for k in ["product_name", "manufacturer", "season_start", "season_end"]
    } == {
        "product_name": "2025-26 Bowman Chrome",
        "manufacturer": "Topps",
        "season_start": 2025,
        "season_end": 2026,
    }
    assert index.card("card:1")["images"][0]["product_name"] == "2025-26 Bowman Chrome"


def test_glossary_card_counts_and_default_union_include_attachment_facts(tmp_path):
    data = snapshot()
    data["official_artifacts"] = [artifact(tmp_path, [fact("attachment")])]
    index = browser(tmp_path, data)
    cards = index.entities(entity_type="card", q="球员甲")
    assert cards["total"] == 2
    assert {r["id"] for r in cards["items"]} == {"attachment", "card:1"}
    assert all(
        r["entity_type"] == "card" and r["source_url"] == "https://example.com/list"
        for r in cards["items"]
    )
    assert index.entities()["total"] == 8
    assert index.entities(q="球员甲")["total"] == 3
    page = index.entities(entity_type="card", offset=1, limit=1)
    assert page["total"] == 2 and page["items"][0]["id"] == "card:1"
