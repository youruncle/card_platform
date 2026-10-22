import json

import httpx
import pytest

from card_platform.config import Settings
from test_player_price_image_evidence import FRONT, BACK, HTML, photo, visual, admit
from test_player_market import observation
from test_player_price_catalog import catalog, fact
from test_player_price_face_observations import faces


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
                        message=dict(
                            role="assistant", content=json.dumps(faces()[len(requests) - 1])
                        ),
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
    assert usage["prompt_tokens"] == 200 and usage["model_calls"] == 2
    assert len(requests) == 2
    for body in requests:
        assert body["model"] == "deepseek-flash" and body["thinking"] == {"type": "disabled"}
        assert len([p for p in body["messages"][-1]["content"] if p["type"] == "image_url"]) == 1
    assert len(usage["observations"]) == 2
    assert json.loads(usage["observations"][1]["raw_response"])["card_number"]["value"] == "201"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["face", "composition"])
async def test_a_paid_but_malformed_face_response_is_archived_before_capture_fails(
    tmp_path, failure_stage
):
    from card_platform.player_prices.image_reader import (
        EvidenceBlobs,
        PriceImageReader,
        PriceImageReadError,
        capture_receipt,
    )

    malformed = '{"role":"front","confidence":90}'
    inputs = faces()
    inputs[1]["serial_stamp"].update(
        value="0/99",
        number_role="unique_serial",
        detail="back actual machine stamp 0/99, a malformed individual stamp",
    )
    inputs[1]["presence"]["serial"]["state"] = "present"
    responses = [malformed] if failure_stage == "face" else [json.dumps(face) for face in inputs]
    returned = []

    def model_answer(request):
        raw = responses[len(returned)]
        returned.append(raw)
        return httpx.Response(
            200,
            json=dict(
                id="fixture",
                object="chat.completion",
                created=1,
                model="deepseek-flash",
                choices=[
                    dict(index=0, finish_reason="stop", message=dict(role="assistant", content=raw))
                ],
                usage=dict(prompt_tokens=100, completion_tokens=20, total_tokens=120),
            ),
        )

    def source_answer(request):
        if "/market/item/123" in request.url.path.lower():
            return httpx.Response(200, text=HTML)
        return httpx.Response(200, content=photo("white" if str(request.url) == FRONT else "gray"))

    store = EvidenceBlobs(tmp_path / "blobs")
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(model_answer)) as model_client,
        httpx.AsyncClient(transport=httpx.MockTransport(source_answer)) as source_client,
    ):
        reader = PriceImageReader(
            Settings(
                vision_api_key="fixture",
                vision_model="deepseek-flash",
                vision_base_url="https://api.deepseek.com",
            ),
            http_client=model_client,
        )
        with pytest.raises(PriceImageReadError) as caught:
            await capture_receipt(observation(), store, reader, client=source_client)
    assert caught.value.model_calls == len(responses)
    assert caught.value.trace_sha256 is not None
    trace = json.loads(store.read(caught.value.trace_sha256))
    assert trace["observations"][-1]["raw_response"] == responses[-1]
    assert trace["model_calls"] == len(responses)


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


@pytest.mark.asyncio
async def test_pair_capture_archives_bound_public_bytes_without_a_model(tmp_path):
    from card_platform.player_prices.image_reader import EvidenceBlobs, capture_pair
    import hashlib

    requests = []

    def answer(request):
        requests.append(str(request.url))
        if "/market/item/123" in request.url.path.lower():
            return httpx.Response(200, text=HTML)
        if str(request.url) == FRONT:
            return httpx.Response(200, content=photo("white"))
        if str(request.url) == BACK:
            return httpx.Response(200, content=photo("gray"))
        raise AssertionError("Only the bound public gallery and two exact images may be fetched")

    store = EvidenceBlobs(tmp_path / "pair")
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        html, images, captured_at = await capture_pair(observation(), store, client=client)
    assert html == HTML and captured_at.tzinfo is not None
    assert [url for url, _, _ in images] == [FRONT, BACK]
    assert requests[0].lower() == "https://cardhobby.com.cn/market/item/123"
    assert requests[1:] == [FRONT, BACK]
    assert store.read(hashlib.sha256(HTML.encode()).hexdigest()) == HTML.encode()
    for _, data, mime in images:
        assert mime == "image/png"
        assert store.read(hashlib.sha256(data).hexdigest()) == data
