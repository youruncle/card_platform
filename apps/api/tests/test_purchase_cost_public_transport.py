"""Production CardHobby resets generic httpx public requests before any vision call."""
import hashlib
from io import BytesIO
import httpx
import pytest
from PIL import Image
from card_platform.purchase_cost_images import PurchaseImageObserver, read_bounded
from card_platform.config import Settings


@pytest.mark.asyncio
async def test_public_observer_uses_working_headers_without_cookies_or_redirects(monkeypatch, tmp_path):
    picture = BytesIO()
    Image.new("RGB", (32, 32), "white").save(picture, format="PNG")
    body = picture.getvalue()
    path = tmp_path / "front.png"
    path.write_bytes(body)
    requests, options = [], []
    original = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        assert request.headers["user-agent"] == "Mozilla/5.0 CardPlatform/1.0"
        assert request.headers["accept"] == "text/html"
        assert "cookie" not in request.headers
        return httpx.Response(200, content=body)

    def factory(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(handle), **kwargs)

    observer = PurchaseImageObserver(Settings(vision_api_key="fixture", upload_dir=str(tmp_path)))
    observer.verifier._target_path = lambda value: path if value else None

    async def gallery(client, line):
        await read_bounded(client, "https://cardhobby.com.cn/market/item/123")
        return {"image_urls": ["https://images.cardhobby.com/card.png"]}

    async def observe_bytes(card, line, order_hash, targets, images, gallery):
        assert images == [body]
        assert hashlib.sha256(targets[0]).digest() == hashlib.sha256(body).digest()
        return {"verdict": "uncertain", "cost_authorized": False}

    observer.gallery = gallery
    observer.observe_bytes = observe_bytes
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    result = await observer.observe({"image_front_url": "/uploads/front.png"}, {}, "order")
    assert len(requests) == 2
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    assert options[0]["timeout"] == 15
    assert result["cost_authorized"] is False


@pytest.mark.asyncio
async def test_owned_original_within_ordinary_upload_budget_is_cached_without_truncation(tmp_path):
    from uuid import uuid4
    from card_platform.purchase_cost_images import validate_photo
    from card_platform.purchase_cost_archive import PurchaseArchive
    picture = BytesIO()
    Image.new('RGB', (32,32), 'white').save(picture, format='JPEG')
    # Reproduced source: valid 3024x4032 JPEG, 10,251,231 bytes, below the
    # existing 10MiB upload budget. Padding keeps this fixture deterministic.
    body = picture.getvalue().ljust(10_251_231, b'\0')
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request:httpx.Response(200,content=body))) as client:
        downloaded = await read_bounded(client,'https://images.cardhobby.com/card.jpg')
    validate_photo(downloaded)
    line=dict(item_id='123',item_url='https://cardhobby.com.cn/market/item/456',
              image_urls=['https://images.cardhobby.com/card.jpg'])
    archive=PurchaseArchive(tmp_path/'private',uuid4())
    archive.save_gallery(line,dict(source_url=line['item_url'],image_urls=line['image_urls']),[downloaded])
    assert archive.gallery(line)[1]==[body]
    assert hashlib.sha256(downloaded).hexdigest()==hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize('declared_size', [True,False])
async def test_source_still_rejects_content_above_ordinary_upload_budget(declared_size):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(11):
                yield b'x' * 1_000_000
    headers={'content-length':'11000000'} if declared_size else {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request:httpx.Response(200,headers=headers,stream=Stream()))) as client:
        with pytest.raises(ValueError,match='response_too_large'):
            await read_bounded(client,'https://images.cardhobby.com/card.jpg')
