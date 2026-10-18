"""Controlled per-face model output for real storage/database contract tests."""
from hashlib import sha256


def consistent_pair(*, player="Synthetic test", product="Test", contents=None):
    return {
        "status": "consistent", "reason_codes": [], "evidence": ["Controlled independent face observations"],
        **{side: {"role": side, "player_names": [player], "product_name": product,
                  "evidence": [f"{side} independently identifies {player} / {product}"]}
           for side in ("front", "back")},
        "image_content_hashes": {side: sha256(content).hexdigest() for side, content in (contents or {}).items()},
        "version": 1,
    }
