from card_platform.knowledge import KnowledgeResolver, normalize_knowledge_text, snapshot_checksum
from card_platform.ingestion.cardhobby import CardHobbyProvider


SNAPSHOT = {
    "version": 7,
    "entities": [
        {
            "id": "product-nt",
            "entity_type": "product",
            "canonical_name": "National Treasures",
            "aliases": ["国宝"],
        },
        {
            "id": "product-one",
            "entity_type": "product",
            "canonical_name": "One and One",
            "aliases": ["One & One", "壹和壹"],
        },
        {
            "id": "insert-first",
            "entity_type": "insert",
            "canonical_name": "First Steps",
            "aliases": ["鞋灯", "第一步"],
        },
        {
            "id": "insert-dream",
            "entity_type": "insert",
            "canonical_name": "Dreamland",
            "aliases": ["梦幻之地"],
        },
        {
            "id": "insert-owl",
            "entity_type": "insert",
            "canonical_name": "Night Owls",
            "aliases": ["夜莺", "猫头鹰"],
        },
        {
            "id": "parallel-red",
            "entity_type": "parallel",
            "canonical_name": "Red",
            "aliases": ["红折", "红平行"],
        },
    ],
    "relations": [
        {
            "subject_entity_id": "insert-dream",
            "relation_type": "conflicts_with",
            "object_entity_id": "insert-owl",
        }
    ],
}


def test_normalization_and_alias_resolution_regressions() -> None:
    resolver = KnowledgeResolver(SNAPSHOT)
    assert normalize_knowledge_text("One & One") == "one one"
    assert resolver.resolve("insert", "鞋灯").canonical_value == "First Steps"
    assert resolver.resolve("insert", "梦幻之地").canonical_value == "Dreamland"
    assert resolver.resolve("insert", "夜莺").canonical_value == "Night Owls"
    assert resolver.resolve("insert", "猫头鹰").canonical_value == "Night Owls"
    assert resolver.resolve("product", "国宝").canonical_value == "National Treasures"
    assert resolver.resolve("product", "One & One").canonical_value == "One and One"


def test_validation_explains_dirty_field_semantics() -> None:
    resolver = KnowledgeResolver(SNAPSHOT)
    issues = resolver.validate_identity(
        {"parallel_name": "RC", "card_number": "12/49", "insert_name": "梦幻之地 夜莺"}
    )
    assert {issue.code for issue in issues} == {
        "attribute_in_parallel",
        "serial_in_card_number",
        "mixed_inserts",
    }
    serial = next(issue for issue in issues if issue.code == "serial_in_card_number")
    assert serial.suggested_field == "serial_total"
    assert serial.suggested_value == 49


def test_query_plan_is_progressive_bilingual_and_bounded() -> None:
    resolver = KnowledgeResolver(SNAPSHOT)
    stages = resolver.build_query_plan(
        {
            "player_name": "Matas Buzelis",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "National Treasures",
            "insert_name": "鞋灯",
            "serial_total": 49,
            "is_rookie": True,
            "is_autograph": True,
            "is_patch": True,
        }
    )
    queries = [stage.query for stage in stages]
    assert queries[0] == "Matas Buzelis National Treasures First Steps 49编"
    assert any("国宝" in query for query in queries)
    assert len(queries) <= 12
    assert all(query != "Matas Buzelis" for query in queries)


def test_snapshot_checksum_is_stable() -> None:
    assert snapshot_checksum(SNAPSHOT) == snapshot_checksum(dict(reversed(list(SNAPSHOT.items()))))


def test_query_plan_never_falls_back_to_player_only() -> None:
    resolver = KnowledgeResolver(SNAPSHOT)
    stages = resolver.build_query_plan(
        {
            "player_name": "VJ Edgecombe",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Topps Midnight",
            "insert_name": "Dreamland",
        }
    )
    assert all(stage.query != "VJ Edgecombe" for stage in stages)


def test_cardhobby_runtime_query_plan_uses_published_aliases() -> None:
    provider = CardHobbyProvider(None, "unused", resolver=KnowledgeResolver(SNAPSHOT))
    identity = {
        "player_name": "Walker Kessler",
        "season_start": 2022,
        "season_end": 2023,
        "product_name": "国宝",
        "insert_name": "鞋灯",
        "serial_total": 49,
        "is_rookie": True,
    }
    queries = [stage.query for stage in provider.resolver.build_query_plan(identity)]
    assert queries[0] == "Walker Kessler National Treasures First Steps 49编"
    assert any("国宝" in query for query in queries)
