from card_platform.comparable_image_verifier import ComparableImageVerifier
from card_platform.config import Settings
from card_platform.comparable_image_verifier import VisualCandidate
from card_platform.comparable_image_verifier import bound_reviewed_visual_decision
from types import SimpleNamespace
from unittest.mock import AsyncMock
from threading import Barrier

import pytest
import base64
import cv2
import numpy as np


def test_target_image_resolves_when_api_runs_from_repository_root(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    upload_dir = tmp_path / "apps" / "api" / "uploads"
    upload_dir.mkdir(parents=True)
    target = upload_dir / "front.jpg"
    target.write_bytes(b"image")

    verifier = ComparableImageVerifier(Settings(upload_dir="uploads"))

    assert verifier._target_path("/uploads/front.jpg") == target
    assert verifier._target_path("/uploads/../front.jpg") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel,expected", [(True, "test-plus"), (False, "test-flash")])
async def test_parallel_review_uses_stronger_model_and_nonexistent_back_guard(parallel, expected):
    verifier = ComparableImageVerifier(Settings(vision_api_key=None, vision_model="test-flash",
                                                vision_plus_model="test-plus"))
    create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content='{"decisions": []}')
    )]))
    verifier.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    await verifier._compare_batch(
        b"target", "image/jpeg", [(VisualCandidate("item", "title", "url"), b"candidate", "image/jpeg")],
        low_resolution_target=False, requires_parallel_match=parallel,
    )
    assert create.call_args.kwargs["model"] == expected
    prompt = create.call_args.kwargs["messages"][1]["content"][0]["text"]
    assert "没有候选背面" in prompt
    assert "镭射反光" in prompt
    assert "compatible不是同款成交确认" in prompt


def test_vision_upload_is_bounded_without_changing_original():
    original = np.zeros((3000, 2000, 3), dtype=np.uint8)
    _, encoded = cv2.imencode(".png", original)
    content = encoded.tobytes()
    url = ComparableImageVerifier._vision_data_url(content, "image/png")
    image = cv2.imdecode(np.frombuffer(base64.b64decode(url.split(",", 1)[1]), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image.shape[:2] == (1536, 1024)
    assert encoded.tobytes() == content
    assert url.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_candidate_geometry_scoring_runs_concurrently(tmp_path, monkeypatch):
    """A slow independent geometry score must not serialize every candidate."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    target_path = upload_dir / "front.jpg"
    image = np.zeros((400, 300, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    target_path.write_bytes(encoded.tobytes())

    verifier = ComparableImageVerifier(Settings(upload_dir=str(upload_dir), vision_api_key=None))
    verifier.client = object()
    candidates = [
        VisualCandidate(
            f"https://cardhobby.com.cn/Market/Item/{index}",
            f"candidate {index}",
            f"https://images.cardhobby.com/{index}.jpg",
        )
        for index in range(3)
    ]

    class Response:
        headers = {"content-type": "image/jpeg"}
        content = encoded.tobytes()

        def raise_for_status(self):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return Response()

    barrier = Barrier(3, timeout=1)

    def score(_target, _candidate):
        barrier.wait()
        return 0, 0, 0

    monkeypatch.setattr("card_platform.comparable_image_verifier.httpx.AsyncClient",
                        lambda **_kwargs: Client())
    monkeypatch.setattr(verifier, "_geometric_template_score", score)
    monkeypatch.setattr(verifier, "_compare_batch", AsyncMock(return_value={}))

    decisions = await verifier.verify("/uploads/front.jpg", candidates)

    assert set(decisions) == {candidate.id for candidate in candidates}


@pytest.mark.parametrize("changed", [None, "target_front_url", "target_back_url", "candidate_image_url", "candidate_source_url", "source"])
def test_review_is_bound_to_current_images_and_explicit_reviewer(changed):
    identity = {"image_front_url": "/uploads/front.jpg", "image_back_url": "/uploads/back.jpg"}
    candidate = VisualCandidate("https://cardhobby.com.cn/Market/Item/123", "title", "https://images.cardhobby.com/123.jpg")
    review = {"source": "codex_paired_image_review", "target_front_url": identity["image_front_url"],
              "target_back_url": identity["image_back_url"], "candidate_image_url": candidate.image_url,
              "candidate_source_url": candidate.id, "verdict": "same", "confidence": ".95",
              "reasons": ["paired images reviewed; same monochrome photo and fixed printing"]}
    from card_platform.comparable_image_verifier import visual_asset_binding
    binding=visual_asset_binding(identity,candidate,b'front',b'back',b'candidate')
    review['evidence_binding']=binding
    if changed:
        review[changed] = "different-asset-or-untrusted-source"
    assert (bound_reviewed_visual_decision(identity, candidate, review, current_binding=binding) is not None) == (changed is None)
