import numpy as np
import pytest

from card_platform import flatbed_local_ocr


@pytest.mark.parametrize("height,width,expected", [
    (1800, 1200, (960, 640)), (1200, 1800, (640, 960)), (400, 600, (400, 600)),
])
def test_orientation_analysis_is_bounded_without_changing_full_resolution_pixels(height, width, expected):
    image = np.random.default_rng(7).integers(0, 256, (height, width, 3), dtype=np.uint8)
    original = image.copy()
    orientation = flatbed_local_ocr.orientation_image(image)
    assert orientation.shape[:2] == expected
    assert np.array_equal(image, original)
    if max(height, width) <= 960:
        assert orientation is image
