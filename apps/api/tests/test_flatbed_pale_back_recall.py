from pathlib import Path

import pytest

from card_platform.flatbed_detection import detect_layout


def test_jordan_seven_backs_include_pale_right_column():
    root = Path(__file__).parents[3]
    paths = list((root / 'data/flatbed-scans').glob('*/6eb10a08-f4bb-4953-8f7f-01e355c8bf4f/5157cbd8-5734-4214-bbd4-2200b132ec75/original.png'))
    if not paths:
        pytest.skip('Local Jordan back scan is not installed')
    layout = detect_layout(paths[0].read_bytes())
    assert len(layout.detections) == 7
    # Both low-saturation backs must have independent crops, not a merged column.
    for x, y in ((0.8, 0.39), (0.8, 0.67)):
        matches = [d for d in layout.detections if d.x <= x <= d.x+d.width and d.y <= y <= d.y+d.height]
        assert len(matches) == 1
        assert matches[0].height < 0.30
