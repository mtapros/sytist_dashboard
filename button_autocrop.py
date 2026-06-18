"""Button auto-crop suggestion helpers with optional MediaPipe support."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AutoCropSuggestion:
    scale: float
    offset: list[int]
    method: str


DEFAULT_AUTOCROP_TEMPLATE_NAME = "Default Face"


@dataclass(frozen=True)
class AutoCropTemplate:
    name: str = DEFAULT_AUTOCROP_TEMPLATE_NAME
    detector_mode: str = "mediapipe_face"
    crop_mode: str = "square"
    top_buffer: float = 0.55
    bottom_buffer: float = 0.55
    left_buffer: float = 0.55
    right_buffer: float = 0.55
    scale_multiplier: float = 1.0
    anchor_x: float = 0.5
    anchor_y: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "detector_mode": self.detector_mode,
            "crop_mode": self.crop_mode,
            "top_buffer": self.top_buffer,
            "bottom_buffer": self.bottom_buffer,
            "left_buffer": self.left_buffer,
            "right_buffer": self.right_buffer,
            "scale_multiplier": self.scale_multiplier,
            "anchor_x": self.anchor_x,
            "anchor_y": self.anchor_y,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AutoCropTemplate":
        data = dict(data or {})

        def _float(key: str, default: float, *, min_value: float | None = None, max_value: float | None = None) -> float:
            try:
                value = float(data.get(key, default))
            except (TypeError, ValueError):
                value = default
            if min_value is not None:
                value = max(min_value, value)
            if max_value is not None:
                value = min(max_value, value)
            return value

        name = str(data.get("name", DEFAULT_AUTOCROP_TEMPLATE_NAME)).strip() or DEFAULT_AUTOCROP_TEMPLATE_NAME
        detector_mode = str(data.get("detector_mode", "mediapipe_face")).strip() or "mediapipe_face"
        crop_mode = str(data.get("crop_mode", "square")).strip() or "square"
        return cls(
            name=name,
            detector_mode=detector_mode,
            crop_mode=crop_mode,
            top_buffer=_float("top_buffer", 0.55, min_value=0.0, max_value=5.0),
            bottom_buffer=_float("bottom_buffer", 0.55, min_value=0.0, max_value=5.0),
            left_buffer=_float("left_buffer", 0.55, min_value=0.0, max_value=5.0),
            right_buffer=_float("right_buffer", 0.55, min_value=0.0, max_value=5.0),
            scale_multiplier=_float("scale_multiplier", 1.0, min_value=0.25, max_value=5.0),
            anchor_x=_float("anchor_x", 0.5, min_value=0.0, max_value=1.0),
            anchor_y=_float("anchor_y", 0.5, min_value=0.0, max_value=1.0),
        )


def default_autocrop_template() -> AutoCropTemplate:
    return AutoCropTemplate()


def default_autocrop_template_store() -> dict[str, Any]:
    default_template = default_autocrop_template()
    return {
        "selected_template": default_template.name,
        "templates": {
            default_template.name: default_template.to_dict(),
        },
    }


def normalize_autocrop_template_store(data: dict[str, Any] | None) -> dict[str, Any]:
    normalized = default_autocrop_template_store()
    saved = dict(data or {})
    templates = saved.get("templates")
    if isinstance(templates, dict):
        normalized_templates: dict[str, dict[str, Any]] = {}
        for template_name, template_data in templates.items():
            payload = dict(template_data or {})
            payload.setdefault("name", str(template_name))
            template = AutoCropTemplate.from_dict(payload)
            normalized_templates[template.name] = template.to_dict()
        if normalized_templates:
            normalized["templates"] = normalized_templates

    selected_template = str(saved.get("selected_template", "")).strip()
    if selected_template and selected_template in normalized["templates"]:
        normalized["selected_template"] = selected_template
    else:
        normalized["selected_template"] = next(iter(normalized["templates"].keys()))
    return normalized


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
        face_bounds = self.detect_face_bounds(source_img)
        if not face_bounds:
            return None
        face_x, face_y, face_w, face_h = face_bounds
        cx = face_x + face_w / 2
        cy = face_y + face_h / 2
        square_size = max(face_w, face_h) * 2.1
        square_x = cx - square_size / 2
        square_y = cy - square_size / 2
        return _clamp_square(square_x, square_y, square_size, source_img.width, source_img.height)

    def detect_face_bounds(self, source_img: Any) -> tuple[float, float, float, float] | None:
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
        return face_x, face_y, face_w, face_h


def suggest_button_autocrop_from_template(
    source_img: Any,
    crop_size: tuple[int, int],
    template: AutoCropTemplate | dict[str, Any] | None,
) -> AutoCropSuggestion:
    """Suggest scale/offset for button design using a saved auto-crop template."""
    fallback = _default_centered_suggestion(source_img, crop_size)
    template_obj = AutoCropTemplate.from_dict(template if isinstance(template, dict) else template.to_dict() if template else None)
    if template_obj.detector_mode == "centered":
        return AutoCropSuggestion(scale=fallback.scale, offset=list(fallback.offset), method="template-centered")

    detector = _MediaPipeFaceSquareDetector()
    face_bounds = detector.detect_face_bounds(source_img)
    if not face_bounds:
        return fallback

    face_x, face_y, face_w, face_h = face_bounds
    rect_x = face_x - face_w * template_obj.left_buffer
    rect_y = face_y - face_h * template_obj.top_buffer
    rect_w = face_w * (1.0 + template_obj.left_buffer + template_obj.right_buffer)
    rect_h = face_h * (1.0 + template_obj.top_buffer + template_obj.bottom_buffer)
    square_size = max(rect_w, rect_h) * template_obj.scale_multiplier
    anchor_x = rect_x + rect_w * template_obj.anchor_x
    anchor_y = rect_y + rect_h * template_obj.anchor_y
    square_x = anchor_x - square_size / 2
    square_y = anchor_y - square_size / 2
    return _square_to_suggestion(
        source_img,
        crop_size,
        x=square_x,
        y=square_y,
        size=square_size,
        method=f"template:{template_obj.name}",
    )


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
