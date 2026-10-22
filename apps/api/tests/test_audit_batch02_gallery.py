import hashlib,json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
import cv2,numpy as np,httpx,pytest
from card_platform.comparable_image_verifier import ComparableImageVerifier,VisualCandidate,VisualDecision
from card_platform.config import Settings


def png(value):
    return cv2.imencode('.png',np.full((400,300,3),value,dtype=np.uint8))[1].tobytes()


@pytest.mark.asyncio
async def test_marker_back_claim_is_bound_to_actually_supplied_support_bytes():
    service=ComparableImageVerifier(Settings(vision_api_key=None))
    marker=lambda ref:dict(status='readable',view='back',text='REFRACTOR',source='images',image_ref=ref)
    evidence=[dict(kind=k,target=t,candidate=t,source='images',both_visible=True,
        target_view='front',candidate_view='front',version_marker_observations=(dict(
            target=marker('target_back'),candidate=marker('candidate_support_1')) if k=='finish' else None))
        for k,t in [('layout','Two portraits over purple text'),('finish','Smooth silver foil with purple text')]]
    data=dict(candidate_id='candidate_1',verdict='same',confidence='.99',design_match='same',finish_match='same',candidate_card_count=1,match_evidence=evidence)
    create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'decisions':[data]})))]))
    service.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    c=VisualCandidate('source','Seller text is not image evidence','https://images.cardhobby.com/front.png')
    row=(c,png(30),'image/png');back=png(90)
    no_back=await service._compare_batch(png(10),'image/png',[row],low_resolution_target=False,target_back=(png(20),'image/png'))
    assert no_back['source'].verdict=='uncertain'
    with_back=await service._compare_batch(png(10),'image/png',[row],low_resolution_target=False,
        target_back=(png(20),'image/png'),candidate_support={'source':[('https://images.cardhobby.com/back.png',back,'image/png')]})
    assert with_back['source'].verdict=='same'
    assert with_back['source'].evidence_binding['candidate_supporting_images']==[dict(url='https://images.cardhobby.com/back.png',sha256=hashlib.sha256(back).hexdigest())]
    content=create.call_args.kwargs['messages'][1]['content']
    assert len([x for x in content if x['type']=='image_url'])==4
    assert 'Seller text is not image evidence' not in str(content)


@pytest.mark.asyncio
async def test_support_downloads_keep_allowlist_and_bind_original_bytes(tmp_path,monkeypatch):
    front,back=png(20),png(80)
    (tmp_path/'front.png').write_bytes(front);(tmp_path/'back.png').write_bytes(back)
    service=ComparableImageVerifier(Settings(upload_dir=str(tmp_path),vision_api_key=None))
    service.client=object()
    candidate=VisualCandidate('source','title','https://images.cardhobby.com/front.png',
        ('https://images.cardhobby.com/back.png','https://unapproved.invalid/private'))
    requests=[];real_client=httpx.AsyncClient
    def handler(request):
        requests.append(str(request.url));return httpx.Response(200,content=back if 'back.png' in str(request.url) else front,headers={'Content-Type':'image/png'})
    monkeypatch.setattr('card_platform.comparable_image_verifier.httpx.AsyncClient',
        lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
    compare=AsyncMock(return_value={'source':VisualDecision(candidate_id='source',verdict='uncertain',confidence=Decimal('.8'))})
    monkeypatch.setattr(service,'_compare_batch',compare)
    result=await service.verify('/uploads/front.png',[candidate],target_back_image_url='/uploads/back.png')
    assert requests==[candidate.image_url,'https://images.cardhobby.com/back.png']
    assert compare.call_args.kwargs['candidate_support']['source']==[('https://images.cardhobby.com/back.png',back,'image/png')]
    assert result['source'].evidence_binding['candidate_supporting_images'][0]['sha256']==hashlib.sha256(back).hexdigest()
