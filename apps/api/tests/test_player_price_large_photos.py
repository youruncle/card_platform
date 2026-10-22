"""Full public originals stay bound when their compressed size exceeds 10 MiB."""

from io import BytesIO
import hashlib
import json

import httpx
from PIL import Image
import pytest

from test_player_price_image_evidence import FRONT, BACK, HTML, photo, fixture
from test_player_price_face_observations import faces, compose


def large_original():
    stream = BytesIO()
    Image.new("RGB", (1800, 2100), "white").save(stream, format="PNG", compress_level=0)
    data = stream.getvalue()
    assert 10 * 1024 * 1024 < len(data) < 16 * 1024 * 1024
    return data


@pytest.mark.asyncio
async def test_price_capture_preserves_a_complete_eleven_megabyte_original_without_resizing(
    tmp_path,
):
    from card_platform.player_prices.image_reader import capture_pair, EvidenceBlobs
    from card_platform.player_prices.image_evidence import make_receipt, admit_receipt

    original, reverse = large_original(), photo("gray")
    obs, _, _, official = fixture(compose(faces()).model_dump(mode="json"))
    store = EvidenceBlobs(tmp_path)

    def answer(request):
        if str(request.url) == FRONT:
            return httpx.Response(200, content=original, headers={"content-type": "image/png"})
        if str(request.url) == BACK:
            return httpx.Response(200, content=reverse, headers={"content-type": "image/png"})
        return httpx.Response(200, text=HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        _, images, captured = await capture_pair(obs, store, client=client)
    assert images[0][1] == original
    assert store.read(hashlib.sha256(original).hexdigest()) == original
    trace = dict(
        contract="price-image-model-trace-v1",
        model_calls=2,
        observations=[
            dict(
                image_index=i,
                image_sha256=hashlib.sha256(image[1]).hexdigest(),
                model="deepseek-flash",
                request_extra={"thinking": {"type": "disabled"}},
                raw_response=json.dumps(faces()[i]),
            )
            for i, image in enumerate(images)
        ],
    )
    trace_sha = store.put(json.dumps(trace).encode())
    receipt = make_receipt(
        obs,
        HTML,
        images,
        compose(faces()),
        model="deepseek-flash",
        usage=dict(observation_trace_sha256=trace_sha),
        now=captured,
    )
    assert receipt["images"][0]["bytes"] == len(original)
    assert (
        admit_receipt(obs, receipt, "Stephen Curry", official, blob_reader=store.read)["status"]
        == "confirmed"
    )
    (tmp_path / (receipt["images"][0]["sha256"] + ".blob")).write_bytes(original[:-1])
    assert (
        admit_receipt(obs, receipt, "Stephen Curry", official, blob_reader=store.read)["status"]
        == "rejected"
    )


def test_nonprice_photo_limit_keeps_its_existing_ten_megabyte_boundary():
    from card_platform.purchase_cost_images import validate_photo

    with pytest.raises(ValueError, match="photo_bytes"):
        validate_photo(large_original())


@pytest.mark.asyncio
async def test_price_capture_still_rejects_a_photo_above_sixteen_megabytes_before_decode():
    from card_platform.player_prices.image_reader import capture_pair

    def answer(request):
        if str(request.url) == FRONT:
            return httpx.Response(200, content=b"x" * (16 * 1024 * 1024 + 1))
        return httpx.Response(200, text=HTML)

    obs = fixture()[0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        with pytest.raises(ValueError, match="response_too_large"):
            await capture_pair(obs, object(), client=client)
