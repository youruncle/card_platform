import pytest
from card_platform.domain.listing_title import title_without_inventory_hash_tail
from card_platform.domain.listing_title import is_multi_card_listing
from card_platform.ingestion.cardhobby import _explicit_hash_card_numbers


@pytest.mark.parametrize('suffix',['【代拍】#1036','【品相见图】 #45891','(consignment) #38281'])
def test_bracketed_inventory_suffix_cannot_contradict_checklist_number(suffix):
    title='1999 Showcase Example Player #PA5 '+suffix
    assert _explicit_hash_card_numbers(title)=={'pa5'}
    assert '#PA5' in title_without_inventory_hash_tail(title)


@pytest.mark.parametrize('title,expected',[('1999 Showcase Example Player #PA8',{'pa8'}),('1999 Example #153',{'153'}),('Example 【代拍】 card #1036',{'1036'}),('Example #1036',{'1036'})])
def test_actual_catalog_claims_still_enforce_number(title,expected):
    assert _explicit_hash_card_numbers(title)==expected


@pytest.mark.parametrize('title',['Example Prizm base LOTS不保品','Example Prizm LOTS #12','Example Prizm lot 3'])
def test_plural_lots_never_become_single_card_price(title):
    assert is_multi_card_listing(title,'Prizm')
