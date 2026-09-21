from __future__ import annotations

import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, _physical_object_envelopes


def _structural(x: float, y: float, width: float, height: float) -> Detection:
    return Detection(
        x,
        y,
        width,
        height,
        0.0,
        0.9,
        evidence=("structural_edge_envelope", "multi_route_instance_corroboration"),
    )


@pytest.mark.parametrize("axis", ("horizontal", "vertical"))
def test_structural_margin_can_cover_holder_inside_a_positive_gap(axis: str) -> None:
    image = np.full((1000, 1000, 3), 224, dtype=np.uint8)
    if axis == "vertical":
        first = _structural(0.10, 0.10, 0.40, 0.20)
        second = _structural(0.10, 0.36, 0.40, 0.20)
    else:
        first = _structural(0.10, 0.10, 0.20, 0.40)
        second = _structural(0.36, 0.10, 0.20, 0.40)

    completed = _physical_object_envelopes(image, [first, second])
    expanded_second = max(
        completed,
        key=lambda item: item.y if axis == "vertical" else item.x,
    )

    if axis == "vertical":
        gap_midpoint = (first.y + first.height + second.y) / 2
        assert gap_midpoint <= expanded_second.y < second.y
    else:
        gap_midpoint = (first.x + first.width + second.x) / 2
        assert gap_midpoint <= expanded_second.x < second.x


@pytest.mark.parametrize("axis", ("horizontal", "vertical"))
def test_structural_margin_does_not_expand_toward_touching_peer(axis: str) -> None:
    image = np.full((1000, 1000, 3), 224, dtype=np.uint8)
    if axis == "vertical":
        first = _structural(0.10, 0.10, 0.40, 0.275)
        second = _structural(0.10, 0.37, 0.40, 0.20)
    else:
        first = _structural(0.10, 0.10, 0.275, 0.40)
        second = _structural(0.37, 0.10, 0.20, 0.40)

    completed = _physical_object_envelopes(image, [first, second])
    isolated_second = max(
        completed,
        key=lambda item: item.y if axis == "vertical" else item.x,
    )

    if axis == "vertical":
        assert isolated_second.y == pytest.approx(second.y)
    else:
        assert isolated_second.x == pytest.approx(second.x)
