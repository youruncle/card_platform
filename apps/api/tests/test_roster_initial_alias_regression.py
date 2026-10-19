from card_platform.domain.comparables import _contains_player
from card_platform.knowledge import KnowledgeResolver
import pytest


TITLE = "2017-18 Panini Status Damian Lillard/Jusuf Nurkic #1 开拓者 达米安 利拉德 CJ 麦科勒姆 Nurkic努尔基奇 三人 折射"
ROSTER = "Damian Lillard, C.J. McCollum, Jusuf Nurkic"


def published():
    return KnowledgeResolver({"version": 18, "entities": [
        {"id": "cj", "entity_type": "player", "canonical_name": "CJ McCollum", "aliases": ["CJ 麦科勒姆"]},
        {"id": "lillard", "entity_type": "player", "canonical_name": "Damian Lillard", "aliases": ["达米安利拉德"]},
        {"id": "nurkic", "entity_type": "player", "canonical_name": "Jusuf Nurkic", "aliases": ["努尔基奇"]},
    ]})


@pytest.mark.parametrize("resolver", [None, published()])
def test_dotted_initial_roster_uses_existing_translated_player_alias(resolver):
    # Dropping the initial-normalized alias lookup recreates #444 player_conflict.
    assert _contains_player(TITLE, ROSTER, resolver=resolver)


@pytest.mark.parametrize("target", ["C.J. McCollum", "C. J. McCollum"])
def test_translated_initial_name_is_not_a_different_person(target):
    assert _contains_player("CJ 麦科勒姆", target, resolver=published())


@pytest.mark.parametrize("title", ["CJ Miles", "C J Miles", "C. J. Miles", "麦克迈尔斯"])
def test_shared_initials_do_not_replace_a_different_surname(title):
    assert not _contains_player(title, "C.J. McCollum", resolver=published())


def test_missing_roster_member_remains_missing():
    assert not _contains_player("2017-18 Status Damian Lillard/Jusuf Nurkic #1", ROSTER, resolver=published())


@pytest.mark.parametrize("canonical,alias", [("ピカチュウ", "Pikachu"),
    ("ルフィ", "Monkey D. Luffy"), ("Nikola Jokić", "Joker")])
def test_published_names_keep_original_unicode_for_knowledge_lookup(canonical, alias):
    resolver = KnowledgeResolver({"version": 18, "entities": [
        {"id": "known", "entity_type": "player", "canonical_name": canonical,
         "aliases": [alias]},
    ]})
    assert _contains_player(alias, canonical, resolver=resolver)
