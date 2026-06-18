"""Initial button auto-crop suggestion helpers with optional MediaPipe support."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AutoCropSuggestion:
    scale: float
    offset: list[int]
    method: str


def _default_centered_suggestion(source_img: Any, crop_size: tuple[int, int]) -> AutoCropSuggestion:
    crop_w, crop_h = crop_size
    initial_scale = max(crop_w / source_img.width, crop_h / source_img.height)
    resized_w = round(source_img.width * initial_scale)
    resized_h = round(source_img.height * initial_scale)
    return AutoCropSuggestion(
        scale=initial_scale,
        offset=[
            round((crop_w - resized_w) / 2),
            round((crop_h - resized_h) / 2),
        ],
        method="centered",
    )


def _clamp_square(x: float, y: float, size: float, width: int, height: int) -> tuple[float, float, float]:
    size = max(1.0, min(size, float(min(width, height))))
    x = max(0.0, min(x, width - size))
    y = max(0.0, min(y, height - size))
    return x, y, size


def _square_to_suggestion(
    source_img: Any,
    crop_size: tuple[int, int],
    *,
    x: float,
    y: float,
    size: float,
    method: str,
) -> AutoCropSuggestion:
    crop_w, crop_h = crop_size
    x, y, size = _clamp_square(x, y, size, source_img.width, source_img.height)
    target_size = max(size, crop_w / 3, crop_h / 3)
    scale = max(crop_w / target_size, crop_h / target_size)
    return AutoCropSuggestion(
        scale=scale,
        offset=[round(-x * scale), round(-y * scale)],
        method=method,
    )


class _MediaPipeFaceSquareDetector:
    """Best-effort MediaPipe detector; returns a face-centered square crop."""

    def __init__(self) -> None:
        self._mp = None
        self._np = None
        try:
            import mediapipe as mp  # type: ignore
            import numpy as np  # type: ignore

            self._mp = mp
            self._np = np
        except Exception:
            self._mp = None
            self._np = None

    @property
    def available(self) -> bool:
        return self._mp is not None and self._np is not None

    def detect_square(self, source_img: Any) -> tuple[float, float, float] | None:
        if not self.available:
            return None
        mp = self._mp
        np = self._np
        image_np = np.asarray(source_img.convert("RGB"))
        try:
            with mp.solutions.face_detection.FaceDetection(
                model_selection=1,
                min_detection_confidence=0.5,
            ) as detector:
                results = detector.process(image_np)
        except Exception:
            return None

        detections = list(getattr(results, "detections", []) or [])
        if not detections:
            return None
        best = max(detections, key=lambda d: float((getattr(d, "score", [0.0]) or [0.0])[0]))
        box = getattr(getattr(best, "location_data", None), "relative_bounding_box", None)
        if box is None:
            return None

        img_w, img_h = source_img.size
        face_x = float(getattr(box, "xmin", 0.0)) * img_w
        face_y = float(getattr(box, "ymin", 0.0)) * img_h
        face_w = float(getattr(box, "width", 0.0)) * img_w
        face_h = float(getattr(box, "height", 0.0)) * img_h
        if face_w <= 0 or face_h <= 0:
            return None

        cx = face_x + face_w / 2
        cy = face_y + face_h / 2
        square_size = max(face_w, face_h) * 2.1
        square_x = cx - square_size / 2
        square_y = cy - square_size / 2
        return _clamp_square(square_x, square_y, square_size, img_w, img_h)


def suggest_button_autocrop(source_img: Any, crop_size: tuple[int, int]) -> AutoCropSuggestion:
    """Suggest scale/offset for button design; falls back to centered crop."""
    fallback = _default_centered_suggestion(source_img, crop_size)
    detector = _MediaPipeFaceSquareDetector()
    square = detector.detect_square(source_img)
    if not square:
        return fallback
    x, y, size = square
    return _square_to_suggestion(
        source_img,
        crop_size,
        x=x,
        y=y,
        size=size,
        method="mediapipe-face",
    )
