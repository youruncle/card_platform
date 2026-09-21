"""Keep scanner overview resolution separate from the requested scan DPI."""

from __future__ import annotations

import math


def resolve_source_resolution(
    width: int, height: int, requested_dpi: int, metadata: dict | None = None
) -> dict:
    """Accept native resolution only when it describes these exact image pixels.

    Saved results can be passed back through this function when deriving a new
    revision. Missing/invalid metadata remains an explicit session fallback;
    image dimensions alone do not prove a physical paper size or resolution.
    """
    raw = metadata if isinstance(metadata, dict) else {}
    capture_kind = raw.get("capture_kind")
    result = {
        "requested_dpi": requested_dpi,
        "effective_dpi": requested_dpi,
        "pixel_width": width,
        "pixel_height": height,
        "capture_kind": capture_kind
        if isinstance(capture_kind, str) and capture_kind in {"preview", "scan", "upload"}
        else "unknown",
        "source": "session_fallback",
        "metadata_status": "unavailable",
    }
    if "actual_dpi" not in raw:
        return result
    actual_dpi = raw.get("actual_dpi")
    if (
        isinstance(actual_dpi, bool)
        or not isinstance(actual_dpi, (int, float))
        or not 25 <= actual_dpi <= 9600
        or not math.isfinite(actual_dpi)
        or not isinstance(raw.get("source"), str)
        or raw.get("source")
        not in {"image_capture_core_resolution", "image_capture_core_overview_resolution"}
    ):
        return {**result, "metadata_status": "invalid_resolution"}
    if (
        type(raw.get("pixel_width")) is not int
        or type(raw.get("pixel_height")) is not int
        or raw["pixel_width"] != width
        or raw["pixel_height"] != height
    ):
        return {**result, "metadata_status": "pixel_dimensions_mismatch"}
    return {
        **result,
        "actual_dpi": actual_dpi,
        "effective_dpi": round(actual_dpi),
        "source": raw["source"],
        "metadata_status": "verified_native_dimensions",
    }
