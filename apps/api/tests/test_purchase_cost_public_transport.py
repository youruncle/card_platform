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
