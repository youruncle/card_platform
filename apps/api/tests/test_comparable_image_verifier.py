from card_platform.comparable_image_verifier import ComparableImageVerifier
from card_platform.config import Settings


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
