#!/usr/bin/env python3
"""Isolated SAM 2 worker. Reads image bytes on stdin and emits refined boxes as JSON."""

from __future__ import annotations

import argparse
import io
import json
import math
from pathlib import Path
import sys
import time

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api/src"))

from card_platform.flatbed_ml_geometry import (  # noqa: E402
    canonical_long_edge_angle,
    consensus_edge_quad,
    strong_axis_aligned_edge_quad,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--boxes-json", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    import torch
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    image = Image.open(io.BytesIO(sys.stdin.buffer.read())).convert("RGB")
    image.thumbnail((1600, 1600))
    pixels = np.array(image)
    height, width = pixels.shape[:2]
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = build_sam2(
        "configs/sam2.1/sam2.1_hiera_t.yaml",
        args.checkpoint,
        device=device,
        apply_postprocessing=False,
    )
    predictor = SAM2ImagePredictor(model)
    started = time.perf_counter()
    predictor.set_image(pixels)
    output = []
    for coarse in json.loads(args.boxes_json):
        x, y = float(coarse["x"]), float(coarse["y"])
        box_width, box_height = float(coarse["width"]), float(coarse["height"])
        prompt = np.array(
            [x * width, y * height, (x + box_width) * width, (y + box_height) * height],
            dtype=np.float32,
        )
        masks, scores, _ = predictor.predict(box=prompt, multimask_output=True)
        choices = []
        for mask, score in zip(masks, scores, strict=True):
            mask_y, mask_x = np.nonzero(mask)
            if not len(mask_x):
                continue
            left, top = int(mask_x.min()), int(mask_y.min())
            right, bottom = int(mask_x.max()) + 1, int(mask_y.max()) + 1
            candidate_width = (right - left) / width
            candidate_height = (bottom - top) / height
            area_ratio = (candidate_width * candidate_height) / (box_width * box_height)
            fill_ratio = float(mask.sum()) / ((right - left) * (bottom - top))
            if not (
                0.68 <= area_ratio <= 1.25 and fill_ratio >= 0.45 and score >= 0.80
            ):
                continue
            rank = float(score) + 0.12 * fill_ratio - 0.18 * abs(math.log(area_ratio))
            contours, _ = cv2.findContours(
                mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            if not contours:
                continue
            rectangle = cv2.minAreaRect(max(contours, key=cv2.contourArea))
            oriented_points = cv2.boxPoints(rectangle)
            normalized_points = [
                [float(point[0] / width), float(point[1] / height)]
                for point in oriented_points
            ]
            choices.append(
                (
                    rank,
                    {
                        "x": left / width,
                        "y": top / height,
                        "width": candidate_width,
                        "height": candidate_height,
                        "angle": float(rectangle[2]),
                        "points": normalized_points,
                        "confidence": float(score),
                        "refined": True,
                    },
                )
            )
        selected = (
            max(choices, key=lambda item: item[0])[1]
            if choices
            else {**coarse, "confidence": 0.0, "refined": False}
        )
        selected["method"] = "sam2_mask" if choices else "classical_fallback"

        if not choices and "cell_extent_fallback" in coarse.get("evidence", []):
            edge = strong_axis_aligned_edge_quad(pixels, coarse)
            if edge is not None:
                edge_points = np.clip(
                    np.asarray(edge["points"], dtype=np.float64), 0.0, 1.0
                )
                edge_left, edge_top = edge_points.min(axis=0)
                edge_right, edge_bottom = edge_points.max(axis=0)
                selected = {
                    "x": float(edge_left),
                    "y": float(edge_top),
                    "width": float(edge_right - edge_left),
                    "height": float(edge_bottom - edge_top),
                    "angle": float(edge["angle"]),
                    "points": edge_points.tolist(),
                    "confidence": 0.82,
                    "refined": True,
                    "method": "strong_axis_edge",
                    "edge_agreement": int(edge["agreement"]),
                }

        # SAM occasionally segments an entire white scanner slot instead of the
        # slightly rotated card inside it. Only replace that suspicious shape
        # when several independent Canny thresholds agree on the same tilted
        # rectangle. This keeps edge noise from overriding a good SAM mask.
        if choices:
            selected_points = np.asarray(selected["points"], dtype=np.float64)
            sam_tilt = abs(canonical_long_edge_angle(selected_points))
            sam_area_ratio = (
                float(selected["width"])
                * float(selected["height"])
                / (box_width * box_height)
            )
            edge = (
                consensus_edge_quad(pixels, coarse)
                if sam_tilt <= 0.75 and sam_area_ratio >= 1.08
                else None
            )
            if edge is not None:
                edge_points = np.clip(
                    np.asarray(edge["points"], dtype=np.float64), 0.0, 1.0
                )
                edge_left, edge_top = edge_points.min(axis=0)
                edge_right, edge_bottom = edge_points.max(axis=0)
                selected = {
                    "x": float(edge_left),
                    "y": float(edge_top),
                    "width": float(edge_right - edge_left),
                    "height": float(edge_bottom - edge_top),
                    "angle": float(edge["angle"]),
                    "points": edge_points.tolist(),
                    "confidence": min(float(selected["confidence"]), 0.92),
                    "refined": True,
                    "method": "edge_consensus",
                    "edge_agreement": int(edge["agreement"]),
                }
        output.append(selected)
    print(
        json.dumps(
            {
                "detections": output,
                "device": device,
                "inference_seconds": round(time.perf_counter() - started, 3),
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
