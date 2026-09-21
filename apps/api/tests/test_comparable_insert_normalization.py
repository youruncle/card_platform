import pytest

from card_platform.domain.card_identity import CardIdentity
from card_platform.domain.comparables import (
    ComparableClassifier,
    ComparableInput,
    ComparableTier,
)
from card_platform.knowledge import KnowledgeResolver


def classifier_with_aliases(use_knowledge: bool, insert: str) -> ComparableClassifier:
    if not use_knowledge:
        return ComparableClassifier()
    return ComparableClassifier(
        KnowledgeResolver(
            {
                "version": 1,
                "entities": [
                    {"id": "target", "entity_type": "insert", "canonical_name": insert},
                    {
                        "id": "other",
                        "entity_type": "insert",
                        "canonical_name": "Finals MVPs",
                        "aliases": ["fmvp", "捧杯", "奖杯"],
                    },
                ],
            }
        )
    )


def target_identity(insert: str) -> CardIdentity:
    return CardIdentity(
        player_name="Example Player",
        season_start=2014,
        season_end=2015,
        product_name="NBA Hoops",
        card_number="14",
        insert=insert,
    )


@pytest.mark.parametrize("use_knowledge", [False, True])
@pytest.mark.parametrize("insert", ["High Honors", "Victory Portraits"])
@pytest.mark.parametrize("description", ["当选FMVP", "捧杯", "奖杯"])
def test_explicit_insert_name_outweighs_short_descriptive_alias(
    use_knowledge: bool,
    insert: str,
    description: str,
) -> None:
    result = classifier_with_aliases(use_knowledge, insert).classify(
        target_identity(insert),
        ComparableInput(
            title=f"2014-15 Panini Hoops Example Player {insert} #14 {description}",
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []
    assert result.field_matches["insert"] is True


@pytest.mark.parametrize("use_knowledge", [False, True])
def test_explicit_different_checklist_name_still_conflicts(use_knowledge: bool) -> None:
    result = classifier_with_aliases(use_knowledge, "High Honors").classify(
        target_identity("High Honors"),
        ComparableInput(
            title="2014-15 Panini Hoops Example Player High Honors Finals MVPs #14 捧杯",
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "insert_conflict" in result.hard_conflicts


@pytest.mark.parametrize("use_knowledge", [False, True])
def test_short_other_insert_alias_without_target_name_is_not_ignored(
    use_knowledge: bool,
) -> None:
    result = classifier_with_aliases(use_knowledge, "High Honors").classify(
        target_identity("High Honors"),
        ComparableInput(title="2014-15 Panini Hoops Example Player #14 FMVP 捧杯"),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "insert_conflict" in result.hard_conflicts


@pytest.mark.parametrize("use_knowledge", [False, True])
def test_target_name_embedded_in_different_word_is_not_explicit(
    use_knowledge: bool,
) -> None:
    result = classifier_with_aliases(use_knowledge, "Victory").classify(
        target_identity("Victory"),
        ComparableInput(title="2014-15 Panini Hoops Example Player VictoryLap #14 FMVP"),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "insert_conflict" in result.hard_conflicts


def test_source_title_retains_exact_insert_despite_award_description() -> None:
    target = CardIdentity(
        player_name="Dirk Nowitzki",
        season_start=2014,
        season_end=2015,
        product_name="NBA HOOPS",
        card_number="14",
        insert="HIGH HONORS",
    )
    result = ComparableClassifier().classify(
        target,
        ComparableInput(
            title=(
                "2014-15 Panini Hoops Dirk Nowitzki 【大彪代拍】独行侠 德克 诺维茨基 "
                "High Honors高光荣誉特卡 带领球队夺队史首冠 当选FMVP 老卡老特 捧杯 B 亚瑟"
            ),
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []
