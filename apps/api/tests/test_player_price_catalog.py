"""Official membership must constrain title-derived identity, never supply finish."""
import gzip
import hashlib
import json
import os

import pytest

from test_player_prices import market_sale


def fact(**overrides):
    attributes = dict(player='Stephen Curry', brand='Panini', product='Prizm',
                      season_start=2024, season_end=2025, card_number='23',
                      insert=None, section='Base', authority='official_checklist',
                      source_url='https://manufacturer.example/checklist.pdf',
                      source_checksum='a'*64, source_locator='page 1 line 23')
    attributes.update(overrides)
    return dict(id='official-card-23', entity_type='card', review_status='approved',
                canonical_name='Official card', attributes=attributes)


def identity(**overrides):
    value = dict(player_name='Stephen Curry', brand_name='Panini', product_name='Prizm',
                 season_start=2024, season_end=None, card_number='23', parallel_name='Base',
                 is_autograph=False, is_memorabilia=False, is_patch=False, is_redemption=False)
    value.update(overrides)
    return value


def catalog(rows, **kwargs):
    from card_platform.player_prices.catalog import PriceCardCatalog
    return PriceCardCatalog(dict(version=21,entities=rows),**kwargs)


def test_confirmed_title_is_canonicalized_against_its_scoped_official_card_with_provenance():
    original = identity()
    result = catalog([fact()]).confirm_title(original, market_sale(
        '2024-25 Panini Prizm Stephen Curry #23 base'))
    assert result and result['season_end'] == 2025
    assert result['parallel_name'] == 'Base'
    assert result['catalog_evidence']['knowledge_version'] == 21
    assert result['catalog_evidence']['source_checksum'] == 'a'*64
    assert 'parallel_name' not in result['catalog_evidence']['fields']
    assert original['season_end'] is None


@pytest.mark.parametrize('attributes', [
    dict(card_number='99'), dict(player='LeBron James'), dict(product='Prizm Deca'),
    dict(season_start=2023,season_end=2024), dict(authority='seller_listing'),
    dict(source_checksum='not a checksum'), dict(source_locator=''),
    dict(section='Base China Variation'),
])
def test_missing_conflicting_or_unverifiable_official_kind_does_not_validate_a_title(attributes):
    assert catalog([fact(**attributes)]).confirm_title(identity(), market_sale(
        '2024-25 Panini Prizm Stephen Curry #23 base')) is None


def test_draft_entity_and_unknown_physical_finish_are_not_filled_from_the_checklist():
    draft = fact()
    draft['review_status']='draft'
    sale = market_sale('2024-25 Panini Prizm Stephen Curry #23 base')
    assert catalog([draft]).confirm_title(identity(), sale) is None
    assert catalog([fact()]).confirm_title(identity(parallel_name=None), sale) is None


def test_english_named_insert_is_preserved_instead_of_collapsing_into_base():
    official = fact(card_number='6',insert='Finalists',section='Finalists')
    c = catalog([official])
    sale = market_sale('2024-25 Panini Prizm Stephen Curry #6 Silver Prizm Finalists')
    result = c.confirm_title(identity(card_number='6',parallel_name='Silver'), sale)
    assert result and result['insert_name'] == 'Finalists'
    assert c.confirm_title(identity(card_number='6'), market_sale(
        '2024-25 Panini Prizm Stephen Curry #6 base')) is None


def test_query_seeds_come_from_checksum_verified_artifacts_and_are_not_price_evidence(tmp_path):
    from card_platform.player_prices.catalog import PriceCardCatalog
    rows=[fact()]
    raw=gzip.compress(json.dumps(rows).encode())
    sha=hashlib.sha256(raw).hexdigest()
    path=tmp_path/f'{sha}.json.gz'
    path.write_bytes(raw)
    artifact=dict(path=path.name,checksum=sha,product='Prizm',season_start=2024,season_end=2025)
    c=PriceCardCatalog(dict(version=21,entities=[],official_artifacts=[artifact]),artifact_root=tmp_path)
    targets=c.discovery_targets('Stephen Curry',[market_sale(
        '2024-25 Panini Prizm Stephen Curry base').model_dump(mode='json')])
    assert targets and targets[0]['identity']['card_number']=='23'
    assert targets[0]['discovery_only'] is True and 'sale_price' not in targets[0]
    path.write_bytes(b'tampered')
    bad=PriceCardCatalog(dict(version=21,entities=[],official_artifacts=[artifact]),artifact_root=tmp_path)
    assert bad.discovery_targets('Stephen Curry',[market_sale(
        '2024-25 Panini Prizm Stephen Curry base').model_dump(mode='json')]) == []
    assert bad.errors


def test_an_unavailable_unrelated_artifact_does_not_poison_the_next_player_refresh(tmp_path):
    from card_platform.player_prices.catalog import PriceCardCatalog
    c = PriceCardCatalog(dict(version=21,entities=[fact()],official_artifacts=[dict(
        path='missing.json.gz',checksum='b'*64,product='Hoops',season_start=2024,season_end=2025,
    )]),artifact_root=tmp_path)
    first = c.session()
    assert first.discovery_targets('Stephen Curry',[dict(title='2024-25 Panini Hoops Stephen Curry base')]) == []
    assert first.errors
    next_player = c.session()
    assert next_player.confirm_title(identity(), market_sale('2024-25 Panini Prizm Stephen Curry #23 base'))
    assert not next_player.errors


@pytest.mark.parametrize('end',[None,2025])
def test_an_unavailable_adjacent_season_is_not_required_by_an_explicit_full_season(tmp_path,end):
    from card_platform.player_prices.catalog import PriceCardCatalog
    c = PriceCardCatalog(dict(version=21,entities=[fact()],official_artifacts=[dict(
        path='missing-2023-24.json.gz',checksum='b'*64,product='Prizm',season_start=2023,season_end=2024,
    )]),artifact_root=tmp_path)
    assert c.confirm_title(identity(season_end=end), market_sale('2024-25 Panini Prizm Stephen Curry #23 base'))
    assert not c.errors


def test_formal_discovery_and_observation_analysis_use_the_scoped_membership_gate():
    from card_platform.player_prices.discovery import identify_sale
    from card_platform.player_prices.service import analyze_observations
    from test_player_market import observation
    c = catalog([fact(card_number='6',insert='Finalists',section='Finalists')])
    title = '2024-25 Panini Prizm Stephen Curry #6 Silver Prizm Finalists'
    result = identify_sale(market_sale(title), 'Stephen Curry', [], catalog=c)
    assert result and result['insert_name'] == 'Finalists'
    rows, coverage = analyze_observations([observation(123,title)], 'Stephen Curry', [], catalog=c)
    assert rows[0]['identity']['insert_name'] == 'Finalists'
    assert coverage['catalog_version'] == 21
    bad = '2024-25 Panini Prizm Stephen Curry #99 base'
    rows, coverage = analyze_observations([observation(124,bad)], 'Stephen Curry', [], catalog=c)
    assert rows == [] and coverage['rejection_reasons']['catalog_unconfirmed'] == 1
    # A checklist cannot replace an absent independent card number in a title.
    assert identify_sale(market_sale('2024-25 Panini Prizm Stephen Curry Silver Prizm'),
                         'Stephen Curry', [], catalog=c) is None


def test_product_scoped_prizm_finish_aliases_share_one_key_without_collapsing_other_technologies():
    from card_platform.player_prices.discovery import identify_sale,card_key
    c = catalog([fact()])
    a = identify_sale(market_sale('2024-25 Panini Prizm Stephen Curry #23 银折'),
                      'Stephen Curry',[],catalog=c)
    b = identify_sale(market_sale('2024-25 Panini Prizm Stephen Curry #23 Silver Prizm'),
                      'Stephen Curry',[],catalog=c)
    assert a and b and card_key(a)==card_key(b)
    assert a['parallel_name']=='Silver Prizm'
    assert identify_sale(market_sale('2024-25 Panini Prizm Stephen Curry #23 Silver Ice Prizm'),
                         'Stephen Curry',[],catalog=c) is None
    # The product, rather than a global colour alias, binds this equivalence.
    optic = catalog([fact(product='Donruss Optic')])
    other = identify_sale(market_sale('2024-25 Panini Donruss Optic Stephen Curry #23 银折'),
                          'Stephen Curry',[],catalog=optic)
    assert other and other['parallel_name']=='Silver' and card_key(other)!=card_key(a)


@pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ, reason='requires isolated PostgreSQL')
def test_publication_changes_replace_the_cached_catalog_and_missing_publication_fails_closed():
    import asyncio
    from uuid import uuid4
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from card_platform.player_prices.catalog import published_catalog

    async def run():
        async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'], row_factory=dict_row) as c:
            try:
                owner = (await (await c.execute(
                    "INSERT INTO app_user(email,display_name) VALUES(%s,'catalog test') RETURNING id",
                    (f'{uuid4()}@example.com',),
                )).fetchone())['id']
                workspace = (await (await c.execute(
                    "SELECT id FROM knowledge_workspace WHERE name='Card Platform'",
                )).fetchone())['id']
                await c.execute('UPDATE knowledge_workspace SET published_version_id=NULL WHERE id=%s', (workspace,))
                assert (await published_catalog(c)).confirm_title(identity(), market_sale(
                    '2024-25 Panini Prizm Stephen Curry #23 base')) is None
                for version, number in [(101, '23'), (102, '24')]:
                    v = (await (await c.execute(
                        'INSERT INTO knowledge_version(workspace_id,version_number,snapshot,checksum,published_by) '
                        'VALUES(%s,%s,%s,%s,%s) RETURNING id',
                        (workspace,version,Jsonb(dict(version=version,entities=[fact(card_number=number)])),
                         str(uuid4()),owner),
                    )).fetchone())['id']
                    await c.execute('UPDATE knowledge_workspace SET published_version_id=%s WHERE id=%s', (v,workspace))
                    current = await published_catalog(c)
                    assert current.version == version and await published_catalog(c) is current
                    sale = market_sale(f'2024-25 Panini Prizm Stephen Curry #{number} base')
                    assert current.confirm_title(identity(card_number=number),sale)
                    if version == 102:
                        assert current.confirm_title(identity(),market_sale(
                            '2024-25 Panini Prizm Stephen Curry #23 base')) is None
                await c.execute('UPDATE knowledge_workspace SET published_version_id=NULL WHERE id=%s', (workspace,))
                assert (await published_catalog(c)).version is None
            finally:
                await c.rollback()
    asyncio.run(run())
