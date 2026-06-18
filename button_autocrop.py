from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


try:
    import mediapipe as mp
except Exception:  # pragma: no cover - optional dependency
    mp = None

try:
    import numpy as np
except Exception:  # pragma: no cover - optional dependency
    np = None


@dataclass(frozen=True)
class CropBox:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _square_box_from_relative_bbox(
    img_w: int,
    img_h: int,
    rel_x: float,
    rel_y: float,
    rel_w: float,
    rel_h: float,
    padding: float = 1.45,
) -> CropBox | None:
    if img_w <= 0 or img_h <= 0:
        return None
    abs_w = max(1.0, rel_w * img_w)
    abs_h = max(1.0, rel_h * img_h)
    side = max(abs_w, abs_h) * max(1.0, padding)
    side = min(side, float(img_w), float(img_h))
    if side <= 1.0:
        return None
    center_x = (rel_x * img_w) + abs_w / 2.0
    center_y = (rel_y * img_h) + abs_h / 2.0
    left = _clamp(center_x - side / 2.0, 0.0, float(img_w) - side)
    top = _clamp(center_y - side / 2.0, 0.0, float(img_h) - side)
    return CropBox(left=left, top=top, right=left + side, bottom=top + side)


def _detect_face_relative_bbox(source_img) -> tuple[float, float, float, float] | None:
    if mp is None or np is None:
        return None
    img_rgb = source_img.convert("RGB")
    arr = np.array(img_rgb)
    with mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.45) as detector:
        result = detector.process(arr)
    detections = getattr(result, "detections", None) or []
    if not detections:
        return None
    bbox = detections[0].location_data.relative_bounding_box
    return float(bbox.xmin), float(bbox.ymin), float(bbox.width), float(bbox.height)


def suggest_button_autocrop(
    source_img,
    *,
    crop_size: tuple[int, int],
    detector: Callable[[object], tuple[float, float, float, float] | None] | None = None,
) -> dict | None:
    """Return initial scale/offset for button design based on face landmarks when available."""
    img_w, img_h = source_img.size
    detect = detector or _detect_face_relative_bbox
    rel_bbox = detect(source_img)
    if not rel_bbox:
        return None
    box = _square_box_from_relative_bbox(img_w, img_h, *rel_bbox)
    if not box:
        return None
    crop_w, crop_h = crop_size
    side = max(box.width, box.height)
    if side <= 1.0:
        return None
    scale = max(crop_w / side, crop_h / side)
    return {
        "scale": float(scale),
        "offset": [int(round(-box.left * scale)), int(round(-box.top * scale))],
    }
