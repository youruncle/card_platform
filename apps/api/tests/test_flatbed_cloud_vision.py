from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from card_platform.card_orientation import OrientationDecision
from card_platform.flatbed_service import FlatbedService


@pytest.mark.asyncio
async def test_unavailable_native_vision_uses_independent_cloud_text_and_rotation(tmp_path):
    observer = AsyncMock()
    observer.classify.return_value = OrientationDecision(
        270, .95, "local_ocr:four_way",
        local_vision={"text": "MICHAEL JORDAN\nUPPER DECK\nNO. 77"},
    )
    service = FlatbedService(str(tmp_path), "missing.swift", cloud_observer=observer)
    service.orientation_classifier.classify = AsyncMock(return_value=None)
    decision, signals = await service.observe_crop(Path("one-card.jpg"))
    assert decision.rotation_degrees == 270
    assert signals["player_name"] == "Michael Jordan"
    assert signals["brand_name"] == "Upper Deck"
    assert signals["card_number"] == "77"
    assert signals["ocr_method"] == "local_ocr:four_way"
    observer.classify.assert_awaited_once_with(Path("one-card.jpg"))


@pytest.mark.asyncio
async def test_strong_native_orientation_keeps_existing_mac_path(tmp_path):
    observer = AsyncMock()
    service = FlatbedService(str(tmp_path), "bridge.swift", cloud_observer=observer)
    native = OrientationDecision(90, .9, "apple_vision_text")
    service.orientation_classifier.classify = AsyncMock(return_value=native)
    assert await service.observe_crop(Path("card.jpg")) == (native, {})
    observer.classify.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloud_failure_is_recorded_instead_of_silent_geometric_fallback(tmp_path):
    observer = AsyncMock()
    observer.classify.side_effect = TimeoutError("model timeout")
    service = FlatbedService(str(tmp_path), "missing.swift", cloud_observer=observer)
    service.orientation_classifier.classify = AsyncMock(return_value=None)
    decision, signals = await service.observe_crop(Path("card.jpg"))
    assert decision is None
    assert signals == {"cloud_vision_failure": "TimeoutError"}


def test_same_player_brand_conflict_cannot_win_over_the_matching_brand():
    from card_platform.flatbed_matching import CardRegion, propose_pairs
    def region(id, y, brand):
        return CardRegion(id, .05, y, .4, .2, signals={"ocr_text": f"MICHAEL JORDAN\n{brand}"})
    result = propose_pairs(
        [region("upper-front", .5, "UPPER DECK"), region("topps-front", .75, "TOPPS")],
        [region("topps-back", .5, "TOPPS"), region("upper-back", .75, "UPPER DECK")],
    )
    assert {(p["front_id"],p["back_id"]) for p in result["pairs"]} == {
        ("upper-front", "upper-back"), ("topps-front", "topps-back"),
    }


def test_topps_and_bowman_do_not_create_a_manufacturer_conflict():
    from card_platform.flatbed_matching import CardRegion, score_pair
    left = CardRegion("f", .1, .1, .2, .3, signals={"ocr_text": "COOPER FLAGG\nBOWMAN"})
    right = CardRegion("b", .1, .1, .2, .3, signals={"ocr_text": "COOPER FLAGG\nTOPPS"})
    assert score_pair(left, right, "identity").identity_status != "conflict"


def test_upright_person_overrides_intentionally_vertical_name_text():
    from card_platform.flatbed_local_ocr import choose_orientation
    decision = choose_orientation({0: .9,90: 0,180: 9.8,270: 0}, {90: (5000,.9)})
    assert decision.rotation_degrees == 90
    assert decision.confidence >= .8


def test_ambiguous_faces_do_not_override_dense_back_text():
    from card_platform.flatbed_local_ocr import choose_orientation
    decision = choose_orientation({0: 0,90: 0,180: 240,270: 3}, {0: (50,.9),90: (48,.9)})
    assert decision.rotation_degrees == 180
    assert decision.method == "rapidocr_text"


def test_split_and_unspaced_printed_names_remain_local_ocr_observations():
    from card_platform.flatbed_pair_signals import extract_pair_signals
    for text, expected in [("STEPHEN\nCURRY", "Stephen Curry"), ("COOPERFLAGG", "Cooper Flagg"), ("BRYANT\nKobe", "Kobe Bryant")]:
        assert extract_pair_signals(text)["player_name"] == expected


@pytest.mark.asyncio
async def test_ocr_only_failure_remains_review_required_after_database_reload(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    import cv2, numpy as np
    from card_platform.flatbed_detection import Detection
    module = "card_platform.flatbed_service"
    report=SimpleNamespace(violations=(),usable=True,needs_review=False,usability_score=1,as_dict=lambda:{"usable":True,"violations":[]})
    monkeypatch.setattr(module+".assess_sheet_crop_quality",lambda *args:[report])
    monkeypatch.setattr(module+".audit_object_crop_integrity",lambda reports,*args,**kwargs:(reports,{"error_codes":[]}))
    monkeypatch.setattr(module+".audit_original_content_integrity",lambda reports,*args,**kwargs:(reports,{"error_codes":[]}))
    monkeypatch.setattr(module+".explain_detection",lambda *args:[])
    owner,session=uuid4(),uuid4();saved={}
    class Result:
        async def fetchone(self): return {"id":session,"owner_id":owner,"dpi":300,"revision":1}
    async def execute(sql,parameters=()):
        if sql.startswith("INSERT INTO flatbed_scan_sheet"):saved["requires_review"]=parameters[-1]
        if sql.startswith("UPDATE flatbed_scan_sheet SET requires_review=true"):saved["requires_review"]=True
        return Result()
    observer=AsyncMock();observer.classify.side_effect=TimeoutError()
    service=FlatbedService(str(tmp_path),"missing.swift",cloud_observer=observer)
    service.orientation_classifier.classify=AsyncMock(return_value=None)
    _,encoded=cv2.imencode('.png',np.full((400,400,3),180,np.uint8))
    result=await service.store_sheet(SimpleNamespace(execute=execute),owner,session,'front',encoded.tobytes(),'image/png',detections_override=[Detection(.1,.1,.5,.7,0,.9)])
    assert result['failures']==['cloud_vision_failed']
    assert result['requires_review'] is True
    assert saved['requires_review'] is True
