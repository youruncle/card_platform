"""Offline Linux OCR and semantic orientation; no model API calls."""

import hashlib
import json
import os
import sys
from pathlib import Path

from card_platform.card_orientation import OrientationDecision
from card_platform.local_vision_process import run_local_vision

MODEL_DIR = Path(os.environ.get("FLATBED_OCR_MODEL_DIR", "/app/ocr-models"))
MODELS = {
    "en_PP-OCRv4_rec.onnx": (
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/rec/en_PP-OCRv4_rec_mobile.onnx",
        "e8770c967605983d1570cdf5352041dfb68fa0c21664f49f47b155abd3e0e318",
    ),
    "yunet.onnx": (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
}


def install_models() -> None:
    from urllib.request import urlopen
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in MODELS.items():
        path = MODEL_DIR / name
        content = path.read_bytes() if path.is_file() else urlopen(url, timeout=90).read()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"OCR model checksum mismatch: {name}")
        path.write_bytes(content)


def choose_orientation(text_votes: dict[int, float], faces: dict[int, tuple[float, float]]) -> OrientationDecision:
    face_order = sorted(faces, key=lambda x: faces[x][0], reverse=True)
    if face_order:
        best = face_order[0]
        second = faces[face_order[1]][0] if len(face_order) > 1 else 0
        if faces[best][0] > 0 and faces[best][0] > second * 1.5 and faces[best][1] >= .85:
            return OrientationDecision(best, faces[best][1], "rapidocr_yunet_face")
    order = sorted(text_votes, key=text_votes.get, reverse=True)
    best = order[0]
    score = text_votes[best]
    margin = (score - text_votes[order[1]]) / max(score, 1)
    strong = score >= 12 and margin >= .3
    return OrientationDecision(best, min(.97, .8 + margin * .15) if strong else .25, "rapidocr_text")


def orientation_image(image):
    """Direction votes need fewer pixels than the final printed-text extraction."""
    import cv2
    scale = min(1, 960 / max(image.shape[:2]))
    return cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image


def observe_image(path: Path) -> dict:
    # Heavy engines live in a bounded subprocess so timeout/cancellation reaps
    # their CPU/RAM instead of leaving an uncancellable inference thread.
    import cv2
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    cv2.setNumThreads(1)
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError("invalid OCR crop")
    for name in MODELS:
        if not (MODEL_DIR / name).is_file():
            raise FileNotFoundError(f"OCR model not installed: {name}")
    engine = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1,
                      det_limit_side_len=1536, det_limit_type="max",
                      rec_model_path=str(MODEL_DIR / "en_PP-OCRv4_rec.onnx"))
    detector = cv2.FaceDetectorYN.create(str(MODEL_DIR / "yunet.onnx"), "", (320, 320), score_threshold=.75)
    def rotate(frame, angle):
        return frame if angle == 0 else cv2.rotate(frame, {90:cv2.ROTATE_90_CLOCKWISE,180:cv2.ROTATE_180,270:cv2.ROTATE_90_COUNTERCLOCKWISE}[angle])
    faces = {}
    for angle in (0, 90, 180, 270):
        frame = rotate(image, angle)
        scale = min(1, 640 / max(frame.shape[:2]))
        frame = cv2.resize(frame, None, fx=scale, fy=scale)
        detector.setInputSize((frame.shape[1], frame.shape[0]))
        _, found = detector.detect(frame)
        valid = [row for row in found if row[-1] >= .85 and row[2]*row[3]/(frame.shape[0]*frame.shape[1]) >= .003] if found is not None else []
        faces[angle] = (max([float(r[2]*r[3]*r[-1]) for r in valid] or [0]), max([float(r[-1]) for r in valid] or [0]))
    votes = {angle: 0.0 for angle in (0, 90, 180, 270)}
    # Text line crops are rectified independently. Their 0/180 classifier plus
    # each quad's vertical axis gives the corresponding whole-card rotation.
    direction_image = orientation_image(image)
    boxes, _ = engine.auto_text_det(direction_image)
    if boxes is not None:
        crops = engine.get_crop_img_list(direction_image, boxes)
        fixed, classes, _ = engine.text_cls(crops)
        texts, _ = engine.text_rec(fixed)
        for box, (text, confidence), (label, cls_confidence) in zip(boxes, texts, classes):
            if confidence < .65:
                continue
            width = max(np.linalg.norm(box[0]-box[1]), np.linalg.norm(box[2]-box[3]))
            height = max(np.linalg.norm(box[0]-box[3]), np.linalg.norm(box[1]-box[2]))
            angle = ((270 if height >= width*1.5 else 0) + (180 if label == "180" and cls_confidence > .9 else 0)) % 360
            votes[angle] += sum(c.isalnum() for c in text) * confidence
    decision = choose_orientation(votes, faces)
    upright = rotate(image, decision.rotation_degrees)
    scale = 1536 / max(upright.shape[:2])
    upright = cv2.resize(upright, None, fx=scale, fy=scale)
    rows, _ = engine(upright, use_cls=True)
    text = "\n".join(row[1] for row in rows or [] if row[2] >= .5)[:8000]
    return {"rotation_degrees": decision.rotation_degrees, "confidence": decision.confidence,
            "method": decision.method, "text": text, "text_votes": votes,
            "engine": "RapidOCR-1.4.4/PP-OCRv4-en/YuNet-2023mar"}


class LinuxLocalOCRObserver:
    async def classify(self, image_path: Path) -> OrientationDecision:
        stdout, _, code = await run_local_vision(sys.executable, "-m", __name__, "--observe", str(image_path), timeout_seconds=45)
        if code:
            raise RuntimeError("Linux local OCR process failed")
        payload = json.loads(stdout)
        rotation, confidence, text = payload["rotation_degrees"], payload["confidence"], payload["text"]
        if type(rotation) is not int or rotation not in (0,90,180,270) or type(confidence) not in (int,float) or not 0 <= confidence <= 1 or not isinstance(text,str):
            raise ValueError("invalid Linux OCR response")
        return OrientationDecision(rotation, confidence, payload["method"], text_count=len(text.splitlines()), local_vision={"text":text,"engine":payload["engine"]})


if __name__ == "__main__":
    if sys.argv[1:] == ["--install-models"]:
        install_models()
    elif len(sys.argv) == 3 and sys.argv[1] == "--observe":
        print(json.dumps(observe_image(Path(sys.argv[2])), ensure_ascii=False))
    else:
        raise SystemExit("expected --install-models or --observe PATH")
