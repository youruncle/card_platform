from card_platform.domain.player_aliases import matching_player_aliases, query_player_aliases
from card_platform.knowledge import KnowledgeResolver
from card_platform.purchase_cost_bundle import purchase_player_aliases
from card_platform.purchase_cost_search import build_purchase_cost_search_plan


def test_hart_chinese_order_recalled_with_safe_identity_alias():
    identity = {'player_name': 'JOSH HART'}
    plan = build_purchase_cost_search_plan(identity, KnowledgeResolver())
    assert '哈特' in plan['keywords']
    assert '约什哈特' in purchase_player_aliases('JOSH HART', KnowledgeResolver())
    assert '哈特' not in matching_player_aliases('josh hart')


def test_shared_surnames_are_discovery_only():
    for name in ('josh hart', 'jason hart', 'jalen williams', 'jaylin williams'):
        assert query_player_aliases(name)
    assert '哈特' not in matching_player_aliases('jason hart')
    assert '威廉姆斯' not in matching_player_aliases('jalen williams')
    assert '威廉姆斯' not in matching_player_aliases('jaylin williams')


def test_published_query_aliases_do_not_enter_identity_resolution():
    resolver = KnowledgeResolver({'version': 7, 'entities': [
        {'id': 'hart', 'entity_type': 'player', 'canonical_name': 'Josh Hart',
         'aliases': ['约什哈特'], 'attributes': {'query_aliases': ['哈特', 'Josh H.']}}
    ]})
    assert 'Josh H.' in purchase_player_aliases('Josh Hart', resolver, discovery=True)
    assert 'Josh H.' not in purchase_player_aliases('Josh Hart', resolver)
    assert resolver.resolve('player', '哈特').entity_id is None


def test_ambiguous_player_alias_in_snapshot_does_not_choose_last_person():
    resolver = KnowledgeResolver({'entities': [
        {'id': str(i), 'entity_type': 'player', 'canonical_name': name, 'aliases': ['哈特']}
        for i, name in enumerate(('Josh Hart', 'Jason Hart'))
    ]})
    assert resolver.resolve('player', '哈特').entity_id is None
    assert '哈特' not in resolver.aliases_for('player', 'Josh Hart')


def test_full_catalog_has_sources_and_tracks_untranslated_players():
    from card_platform.domain.nba_player_catalog import nba_player_manifest
    from card_platform.knowledge_manifest import validate_manifest
    manifest = nba_player_manifest()
    report = validate_manifest(manifest)
    assert report.publishable, report.conflicts
    assert len(manifest['entities']) >= 5205
    assert sum(e['attributes']['chinese_coverage'] == 'sourced' for e in manifest['entities']) >= 4200
    assert any(e['attributes']['chinese_coverage'] == 'missing_source' for e in manifest['entities'])
    for entity in manifest['entities']:
        assert not set(a['value'] for a in entity['aliases']) & set(entity['attributes']['query_aliases'])


def test_published_hart_aliases_keep_same_purchase_search_behavior():
    from card_platform.domain.nba_player_catalog import nba_player_manifest
    entity = next(e for e in nba_player_manifest()['entities'] if e['canonical_name'] == 'Josh Hart')
    resolver = KnowledgeResolver({'entities': [{**entity, 'id': entity['key'], 'aliases': [a['value'] for a in entity['aliases']]}]})
    assert '哈特' in build_purchase_cost_search_plan({'player_name': 'Josh Hart'}, resolver)['keywords']
    assert '约什哈特' in purchase_player_aliases('Josh Hart', resolver)
    assert '哈特' not in purchase_player_aliases('Josh Hart', resolver)


def test_discovery_does_not_leak_into_multi_player_identity_gate():
    from card_platform.ingestion.cardhobby import _title_matches_player
    assert not _title_matches_player({'player_name': 'Josh Hart, Jalen Williams'}, '哈特 威廉姆斯')


def test_nba_manifest_visible_in_knowledge_import_catalog():
    from card_platform.knowledge_batch_catalog import manifest_catalog
    assert 'nba-player-aliases-v1' in manifest_catalog()


def test_sons_chinese_names_do_not_identify_fathers():
    from card_platform.ingestion.cardhobby import _title_matches_player
    from card_platform.domain.comparables import _contains_player
    for father, son in [('Gary Payton', '加里佩顿二世'), ('Glenn Robinson', '格伦罗宾逊三世')]:
        assert not _title_matches_player({'player_name': father}, son)
        assert not _contains_player(son, father)
        assert all(alias not in son for alias in purchase_player_aliases(father, KnowledgeResolver()))
