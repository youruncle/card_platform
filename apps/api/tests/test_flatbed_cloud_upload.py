import io
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

from card_platform import flatbed_api
from card_platform.auth import current_user


@pytest.mark.asyncio
@pytest.mark.parametrize('metadata, expected_status', [
    ('{"actual_dpi":200,"pixel_width":30,"pixel_height":40,"capture_kind":"preview","source":"image_capture_core_overview_resolution"}', 201),
    (None, 201), ('not json',422), ('[]',422), ('null',422), ('x'*4097,422),
])
async def test_cloud_upload_accepts_bounded_metadata_with_current_owner(monkeypatch,metadata,expected_status):
    app=FastAPI();app.include_router(flatbed_api.router)
    owner_id,session_id=uuid4(),uuid4()
    app.dependency_overrides[current_user]=lambda:SimpleNamespace(id=owner_id)
    app.dependency_overrides[flatbed_api.connection]=lambda:None
    service=SimpleNamespace(store_sheet=AsyncMock(return_value={'id':'sheet','regions':[],'failures':[]}))
    monkeypatch.setattr(flatbed_api,'service',lambda:service)
    image=io.BytesIO();Image.new('RGB',(30,40)).save(image,format='PNG')
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post(f'/v1/flatbed/sessions/{session_id}/sheets/front',files={'image':('scan.png',image.getvalue(),'image/png')},data={} if metadata is None else {'source_resolution':metadata})
    assert response.status_code==expected_status
    if expected_status==201:
        args=service.store_sheet.call_args.args
        assert args[1:4]==(owner_id,session_id,'front')
        assert args[4]==image.getvalue()
        if metadata:
            assert service.store_sheet.call_args.kwargs['source_resolution']['actual_dpi']==200
    else:
        service.store_sheet.assert_not_awaited()
