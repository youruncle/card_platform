import json

import httpx
import pytest

from card_platform.config import Settings
from test_player_price_image_evidence import FRONT, BACK, HTML, photo, visual, admit
from test_player_market import observation
from test_player_price_catalog import catalog, fact


@pytest.mark.asyncio
async def test_real_model_adapter_sends_bound_images_disabled_thinking_and_validates_json():
    from card_platform.player_prices.image_reader import PriceImageReader

    requests = []

    def answer(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=dict(
                id="fixture",
                object="chat.completion",
                created=1,
                model="deepseek-flash",
                choices=[
                    dict(
                        index=0,
                        finish_reason="stop",
                        message=dict(role="assistant", content=json.dumps(visual())),
                    )
                ],
                usage=dict(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        reader = PriceImageReader(
            Settings(
                vision_api_key="fixture",
                vision_model="deepseek-flash",
                vision_base_url="https://api.deepseek.com",
            ),
            http_client=client,
        )
        result, usage = await reader.observe(
            [(photo("white"), "image/png"), (photo("gray"), "image/png")]
        )
    assert result.card_number == "201" and result.parallel_name == "Refractor"
    assert usage["prompt_tokens"] == 100
    body = requests[0]
    assert body["model"] == "deepseek-flash" and body["thinking"] == {"type": "disabled"}
    assert len([p for p in body["messages"][-1]["content"] if p["type"] == "image_url"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("primary", [FRONT, FRONT + "?imageView2/2/w/800"])
async def test_source_capture_uses_exact_gallery_archives_bytes_and_builds_admissible_receipt(
    tmp_path,
    primary,
):
    from card_platform.player_prices.image_reader import EvidenceBlobs, capture_receipt

    from card_platform.public_source_catalog import observe_public_sale

    raw = observation(title="2025-26 Topps Chrome Stephen Curry 银折")["raw_row"]
    obs = observe_public_sale({**raw, "TitImg": primary})

    def answer(request):
        if "/market/item/123" in request.url.path.lower():
            return httpx.Response(200, text=HTML + '<img src="https://private.example/avatar.jpg">')
        if str(request.url) == FRONT:
            return httpx.Response(
                200, content=photo("white"), headers={"Content-Type": "image/png"}
            )
        if str(request.url) == BACK:
            return httpx.Response(200, content=photo("gray"), headers={"Content-Type": "image/png"})
        raise AssertionError("unbound image requested")

    class Reader:
        model = "deepseek-flash"

        async def observe(self, images):
            from card_platform.player_prices.image_evidence import PriceImageResult

            return PriceImageResult.model_validate(visual()), {}

    store = EvidenceBlobs(tmp_path / "images")
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        receipt = await capture_receipt(obs, store, Reader(), client=client)
    official = catalog(
        [
            fact(
                brand="Topps",
                product="Topps Chrome",
                season_start=2025,
                season_end=2026,
                card_number="201",
            )
        ]
    )
    decision = admit(
        obs,
        receipt,
        {
            sha: store.read(sha)
            for sha in [
                receipt["gallery"]["raw_html_sha256"],
                *[image["sha256"] for image in receipt["images"]],
            ]
        },
        official,
    )
    assert decision["status"] == "confirmed"


@pytest.mark.asyncio
async def test_single_image_or_redirect_does_not_spend_a_model_call(tmp_path):
    from card_platform.player_prices.image_reader import EvidenceBlobs, capture_receipt

    class Reader:
        model = "deepseek-flash"

        async def observe(self, images):
            raise AssertionError("unverified gallery must not invoke model")

    for response in [
        httpx.Response(302, headers={"Location": "https://private.example/secret"}),
        httpx.Response(200, text=HTML.replace(f'<img src="{BACK}">', "")),
        httpx.Response(
            200,
            text=HTML.replace(
                "</div>", '<img src="https://images.cardhobby.com/another-card.jpg"></div>'
            ),
        ),
    ]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: response)) as client:
            with pytest.raises((ValueError, httpx.HTTPError)):
                await capture_receipt(
                    observation(), EvidenceBlobs(tmp_path / "images"), Reader(), client=client
                )
