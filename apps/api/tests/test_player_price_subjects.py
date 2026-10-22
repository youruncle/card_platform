from datetime import datetime, timezone
from uuid import UUID

from card_platform.player_prices.discovery import card_key


def test_reviewed_full_name_aliases_share_a_price_key_but_query_nicknames_do_not():
    identity = dict(player_name='Luka Doncic', season_start=2024, product_name='Prizm',
                    card_number='89', parallel_name='Base')
    assert card_key(identity) == card_key(identity | {'player_name': 'LUKA DONČIĆ'})
    assert card_key(identity | {'player_name': 'LeBron James'}) != card_key(
        identity | {'player_name': '詹姆斯'})
    assert card_key(identity | {'player_name': 'Gary Payton'}) != card_key(
        identity | {'player_name': 'Gary Payton II'})


def row(number, name, **extra):
    return dict(id=UUID(int=number), full_name=name, birth_date=None, nba_person_id=None,
                created_at=datetime(2020, 1, number, tzinfo=timezone.utc), **extra)


def test_subject_index_uses_oldest_public_entity_and_keeps_all_private_ids_untouched():
    from card_platform.player_prices.subjects import SubjectIndex
    first, second = row(1, 'VICTOR WEMBANYAMA'), row(2, 'Victor Wembanyama')
    index = SubjectIndex([second, first])
    subject = index.get(second['id'])
    assert subject['id'] == first['id'] and subject['full_name'] == 'Victor Wembanyama'
    assert set(subject['member_ids']) == {first['id'], second['id']}
    assert index.for_ids([first['id'], second['id']]) == [subject]
    assert first['full_name'] == 'VICTOR WEMBANYAMA'


def test_conflicting_birthdates_and_explicit_nba_ids_do_not_merge():
    from card_platform.player_prices.subjects import SubjectIndex
    from datetime import date
    first, second = row(1, 'Exact Testperson'), row(2, 'EXACT TESTPERSON')
    first['birth_date'], second['birth_date'] = date(1980,1,1), date(1990,1,1)
    third = row(3, 'Victor Wembanyama')
    third['nba_person_id'] = 999  # Contradicts the reviewed name, so stays isolated.
    fourth = row(4, 'VICTOR WEMBANYAMA')
    index = SubjectIndex([first,second,third,fourth])
    assert len(index.for_ids([r['id'] for r in [first,second,third,fourth]])) == 4


def test_ambiguous_query_only_name_does_not_merge_two_unidentified_people():
    from card_platform.player_prices.subjects import SubjectIndex
    first, second = row(1, 'Steven Smith'), row(2, 'STEVEN SMITH')
    index = SubjectIndex([first,second])
    assert len(index.for_ids([first['id'],second['id']])) == 2


def test_explicit_reviewed_nba_id_supplies_search_name_without_changing_the_oldest_uuid():
    from card_platform.player_prices.subjects import SubjectIndex
    first, second = row(1, 'S. Curry'), row(2, 'Stephen Curry')
    first['nba_person_id'] = 201939
    subject = SubjectIndex([first,second]).get(first['id'])
    assert subject['id'] == first['id']
    assert subject['full_name'] == 'Stephen Curry'
    assert set(subject['member_ids']) == {first['id'],second['id']}
    assert first['full_name'] == 'S. Curry'
