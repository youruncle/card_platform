from test_codex_joint_valuation import fixture, evaluate
import pytest


def test_joint_view_cannot_override_latest_insufficient_job():
    item,snapshot,reviews,fx=fixture()
    item['valuation_status']='insufficient_evidence'
    result=evaluate(item,snapshot,reviews,fx)
    assert result['status']!='joint_recalculated' and result['fair_value'] is None


def test_legacy_text_review_without_two_market_research_cannot_create_current_price():
    item,snapshot,reviews,fx=fixture()
    result=evaluate(item,snapshot,reviews,fx)
    assert result['fair_value'] is None


def current_bound_research_case():
    from test_codex_sales_research import research, NOW
    from card_platform.domain.codex_joint_valuation import joint_valuation
    packet=research();packet['collection_item_id']='item-1'
    item={**packet['identity'], 'id':'item-1', 'image_front_url':'/uploads/f.jpg',
          'image_back_url':'/uploads/b.jpg', 'valuation_status':'succeeded'}
    snapshot=dict(id='snapshot',analysis_version=1,identity_confidence=.95,
        raw_result=dict(collection_item_id='item-1',identity=packet['identity'],
            market_research=packet,research_captures_verified=True,
            input_images=[dict(side=side,url=f'/uploads/{letter}.jpg',sha256=packet[f'{side}_sha256'])
                          for side,letter in [('front','f'),('back','b')]]))
    return packet,item,snapshot,NOW


def test_current_research_can_supply_item_evidence_after_identity_and_image_binding():
    from card_platform.domain.codex_joint_valuation import joint_valuation
    packet,item,snapshot,now=current_bound_research_case()
    result=joint_valuation(item,[snapshot],None,[],'CNY',reviews={},fx={},
        image_hashes={'front':packet['front_sha256'],'back':packet['back_sha256']},now=now)
    assert result['status']=='joint_recalculated'
    assert len(result['accepted_evidence'])==3


def test_research_packet_from_other_image_bytes_cannot_be_rebound_by_snapshot():
    from card_platform.domain.codex_joint_valuation import joint_valuation
    packet,item,snapshot,now=current_bound_research_case()
    current_hashes={'front':packet['front_sha256'],'back':packet['back_sha256']}
    packet['front_sha256']='a'*64
    result=joint_valuation(item,[snapshot],None,[],'CNY',reviews={},fx={},image_hashes=current_hashes,now=now)
    assert result['fair_value'] is None


@pytest.mark.parametrize('change', ['item', 'serial', 'identity'])
def test_research_packet_cannot_supply_another_target(change):
    from card_platform.domain.codex_joint_valuation import joint_valuation
    packet,item,snapshot,now=current_bound_research_case()
    if change == 'item':
        packet['collection_item_id']='other-item'
    elif change == 'serial':
        item.update(serial_number=97, serial_total=199)
        packet['identity']={**packet['identity'], 'serial_number':96, 'serial_total':199}
        snapshot['raw_result']['identity']={**packet['identity'], 'serial_number':97}
        for i,sale in enumerate(packet['sales']):
            sale.update(identity=packet['identity'], serial_number=98+i, serial_total=199)
    else:
        # Keep the snapshot's identity valid while substituting the research packet.
        packet['identity']={**packet['identity'], 'brand_name':'Topps'}
        for sale in packet['sales']:
            sale['identity']=packet['identity']
    result=joint_valuation(item,[snapshot],None,[],'CNY',reviews={},fx={},
        image_hashes={'front':packet['front_sha256'],'back':packet['back_sha256']},now=now)
    assert result['fair_value'] is None
