"""Button auto-crop suggestion helpers with optional local MediaPipe Tasks support."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import threading
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoCropSuggestion:
    scale: float
    offset: list[int]
    method: str
    status: str = "ok"
    status_message: str = ""
    face_bounds: tuple[float, float, float, float] | None = None


DEFAULT_AUTOCROP_TEMPLATE_NAME = "Default Face"
_LOCAL_FACE_MODEL_ENV = "SYTIST_MEDIAPIPE_FACE_MODEL"
_LOCAL_FACE_MODEL_FILENAME = "blaze_face_short_range.tflite"
DEFAULT_MIN_DETECTION_CONFIDENCE = 0.3
DEFAULT_FACE_WIDTH_RATIO = 0.25
DEFAULT_FACE_CENTER_X_RATIO = 0.5
DEFAULT_FACE_BOTTOM_Y_RATIO = 0.5


@dataclass(frozen=True)
class AutoCropTemplate:
    name: str = DEFAULT_AUTOCROP_TEMPLATE_NAME
    detector_mode: str = "mediapipe_face"
    crop_mode: str = "square"
    face_width_ratio: float = DEFAULT_FACE_WIDTH_RATIO
    face_center_x_ratio: float = DEFAULT_FACE_CENTER_X_RATIO
    face_bottom_y_ratio: float = DEFAULT_FACE_BOTTOM_Y_RATIO
    min_detection_confidence: float = DEFAULT_MIN_DETECTION_CONFIDENCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "detector_mode": self.detector_mode,
            "crop_mode": self.crop_mode,
            "face_width_ratio": self.face_width_ratio,
            "face_center_x_ratio": self.face_center_x_ratio,
            "face_bottom_y_ratio": self.face_bottom_y_ratio,
            "min_detection_confidence": self.min_detection_confidence,
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

        legacy_left = _float("left_buffer", 0.55, min_value=0.0, max_value=5.0)
        legacy_right = _float("right_buffer", 0.55, min_value=0.0, max_value=5.0)
        legacy_top = _float("top_buffer", 0.55, min_value=0.0, max_value=5.0)
        legacy_bottom = _float("bottom_buffer", 0.55, min_value=0.0, max_value=5.0)
        legacy_scale = _float("scale_multiplier", 1.0, min_value=0.25, max_value=5.0)
        legacy_anchor_x = _float("anchor_x", 0.5, min_value=0.0, max_value=1.0)
        legacy_anchor_y = _float("anchor_y", 0.5, min_value=0.0, max_value=1.0)

        has_new_positioning = any(
            key in data
            for key in ("face_width_ratio", "face_center_x_ratio", "face_bottom_y_ratio")
        )

        if has_new_positioning:
            face_width_ratio = _float("face_width_ratio", DEFAULT_FACE_WIDTH_RATIO, min_value=0.05, max_value=0.9)
            face_center_x_ratio = _float("face_center_x_ratio", DEFAULT_FACE_CENTER_X_RATIO, min_value=0.0, max_value=1.0)
            face_bottom_y_ratio = _float("face_bottom_y_ratio", DEFAULT_FACE_BOTTOM_Y_RATIO, min_value=0.0, max_value=1.0)
        else:
            legacy_rect_w = 1.0 + legacy_left + legacy_right
            face_width_ratio = DEFAULT_FACE_WIDTH_RATIO / max(0.01, legacy_rect_w * legacy_scale)
            face_width_ratio = max(0.05, min(0.9, face_width_ratio))
            face_center_x_ratio = max(0.0, min(1.0, legacy_anchor_x))
            face_bottom_y_ratio = max(0.0, min(1.0, legacy_anchor_y + ((0.5 - legacy_anchor_y) + (legacy_bottom * 0.5))))

        return cls(
            name=name,
            detector_mode=detector_mode,
            crop_mode=crop_mode,
            face_width_ratio=face_width_ratio,
            face_center_x_ratio=face_center_x_ratio,
            face_bottom_y_ratio=face_bottom_y_ratio,
            min_detection_confidence=_float(
                "min_detection_confidence",
                DEFAULT_MIN_DETECTION_CONFIDENCE,
                min_value=0.05,
                max_value=0.95,
            ),
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


def format_autocrop_status_line(suggestion: AutoCropSuggestion) -> str:
    status_prefix = {
        "centered_mode": "Centered mode selected",
        "mediapipe_unavailable": "MediaPipe unavailable",
        "no_face_detected": "No face detected",
        "face_crop_applied": "Face crop applied",
    }.get(suggestion.status, suggestion.status.replace("_", " ").strip().title() or "Auto-crop status")
    detail = (suggestion.status_message or "").strip()
    return f"{status_prefix}: {detail}" if detail else status_prefix


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
        status="centered",
        status_message="Centered fallback crop.",
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
    status: str = "ok",
    status_message: str = "",
    face_bounds: tuple[float, float, float, float] | None = None,
) -> AutoCropSuggestion:
    crop_w, crop_h = crop_size
    x, y, size = _clamp_square(x, y, size, source_img.width, source_img.height)
    target_size = max(size, crop_w / 3, crop_h / 3)
    scale = max(crop_w / target_size, crop_h / target_size)
    return AutoCropSuggestion(
        scale=scale,
        offset=[round(-x * scale), round(-y * scale)],
        method=method,
        status=status,
        status_message=status_message,
        face_bounds=face_bounds,
    )


def _face_bounds_to_suggestion(
    source_img: Any,
    crop_size: tuple[int, int],
    *,
    face_bounds: tuple[float, float, float, float],
    face_width_ratio: float,
    face_center_x_ratio: float,
    face_bottom_y_ratio: float,
    method: str,
    status: str = "ok",
    status_message: str = "",
) -> AutoCropSuggestion:
    crop_w, crop_h = crop_size
    face_x, face_y, face_w, face_h = face_bounds
    if face_w <= 0 or face_h <= 0:
        return _default_centered_suggestion(source_img, crop_size)

    desired_face_width = max(1.0, crop_w * face_width_ratio)
    scale = max(desired_face_width / face_w, crop_h / max(float(source_img.height), 1.0) * 0.01)

    target_center_x = crop_w * face_center_x_ratio
    target_bottom_y = crop_h * face_bottom_y_ratio
    face_center_x = face_x + face_w / 2.0
    face_bottom_y = face_y + face_h

    offset_x = target_center_x - (face_center_x * scale)
    offset_y = target_bottom_y - (face_bottom_y * scale)

    return AutoCropSuggestion(
        scale=scale,
        offset=[round(offset_x), round(offset_y)],
        method=method,
        status=status,
        status_message=status_message,
        face_bounds=face_bounds,
    )


def _candidate_face_model_paths() -> list[Path]:
    paths: list[Path] = []

    import os

    env_value = str(os.environ.get(_LOCAL_FACE_MODEL_ENV, "") or "").strip()
    if env_value:
        paths.append(Path(env_value).expanduser())

    here = Path(__file__).resolve().parent
    repo_root = here
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "requirements.txt").exists() or (parent / "pyproject.toml").exists():
            repo_root = parent
            break

    candidate_dirs = [
        here,
        here / "models",
        repo_root,
        repo_root / "models",
        Path.cwd(),
        Path.cwd() / "models",
        Path.home() / ".volumetoolkit_vtk" / "models",
    ]

    for directory in candidate_dirs:
        paths.append(directory / _LOCAL_FACE_MODEL_FILENAME)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser())
        if key not in seen:
            unique.append(path.expanduser())
            seen.add(key)
    return unique


class _MediaPipeFaceSquareDetector:
    """Best-effort local MediaPipe Tasks detector; returns face bounds and square crop."""

    _shared_lock = threading.Lock()
    _shared_detectors: dict[float, Any] = {}
    _shared_import_error: str | None = None
    _shared_model_path: Path | None = None

    def __init__(self, *, min_detection_confidence: float = DEFAULT_MIN_DETECTION_CONFIDENCE) -> None:
        self._mp = None
        self._np = None
        self.min_detection_confidence = max(0.05, min(float(min_detection_confidence), 0.95))
        try:
            import mediapipe as mp  # type: ignore
            import numpy as np  # type: ignore

            self._mp = mp
            self._np = np
            logger.info("MediaPipe and numpy imports succeeded for button auto-crop.")
        except Exception as exc:
            self._mp = None
            self._np = None
            self._set_import_error(f"MediaPipe/numpy import failed: {exc}")
            logger.warning("MediaPipe/numpy imports unavailable for button auto-crop: %s", exc)

    @classmethod
    def _set_import_error(cls, message: str) -> None:
        with cls._shared_lock:
            cls._shared_import_error = message

    @classmethod
    def unavailable_reason(cls) -> str:
        return cls._shared_import_error or "MediaPipe face detector is not available."

    @property
    def available(self) -> bool:
        return self._mp is not None and self._np is not None and self._get_detector() is not None

    def _resolve_model_path(self) -> Path | None:
        for path in _candidate_face_model_paths():
            try:
                if path.is_file():
                    logger.info("Using local MediaPipe face model: %s", path)
                    return path
            except Exception:
                continue
        return None

    def _get_detector(self):
        if self._mp is None:
            return None
        with self.__class__._shared_lock:
            confidence_key = round(self.min_detection_confidence, 2)
            cached = self.__class__._shared_detectors.get(confidence_key)
            if cached is not None:
                return cached

            model_path = self._resolve_model_path()
            if model_path is None:
                searched = ", ".join(str(p) for p in _candidate_face_model_paths())
                self.__class__._shared_import_error = (
                    "Local MediaPipe face model not found. "
                    f"Set {_LOCAL_FACE_MODEL_ENV} or place {_LOCAL_FACE_MODEL_FILENAME} in a known local models folder. "
                    f"Searched: {searched}"
                )
                logger.warning(self.__class__._shared_import_error)
                return None

            try:
                mp = self._mp
                BaseOptions = mp.tasks.BaseOptions
                FaceDetector = mp.tasks.vision.FaceDetector
                FaceDetectorOptions = mp.tasks.vision.FaceDetectorOptions
                RunningMode = mp.tasks.vision.RunningMode

                options = FaceDetectorOptions(
                    base_options=BaseOptions(model_asset_path=str(model_path)),
                    running_mode=RunningMode.IMAGE,
                    min_detection_confidence=self.min_detection_confidence,
                )
                detector = FaceDetector.create_from_options(options)
                self.__class__._shared_detectors[confidence_key] = detector
                self.__class__._shared_model_path = model_path
                self.__class__._shared_import_error = None
                logger.info(
                    "Initialized local MediaPipe Tasks face detector from %s with confidence %.2f.",
                    model_path,
                    self.min_detection_confidence,
                )
            except Exception as exc:
                self.__class__._shared_detectors.pop(confidence_key, None)
                self.__class__._shared_import_error = f"Failed to initialize local MediaPipe face detector: {exc}"
                logger.warning("Failed to initialize local MediaPipe face detector: %s", exc)
                return None

            return self.__class__._shared_detectors.get(confidence_key)

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
        detector = self._get_detector()
        if detector is None or self._mp is None or self._np is None:
            logger.warning("MediaPipe Tasks detection skipped because dependencies or local model are unavailable.")
            return None

        mp = self._mp
        np = self._np
        logger.info("Starting local MediaPipe Tasks face detection for image size %sx%s.", source_img.width, source_img.height)
        rgb = source_img.convert("RGB") if getattr(source_img, "mode", "") != "RGB" else source_img
        image_np = np.asarray(rgb)
        try:
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_np)
            results = detector.detect(mp_image)
        except Exception as exc:
            logger.warning("Local MediaPipe Tasks face detection failed: %s", exc)
            return None

        detections = list(getattr(results, "detections", []) or [])
        logger.info("Local MediaPipe Tasks face detection returned %s detections.", len(detections))
        if not detections:
            return None

        def _score(det: Any) -> float:
            categories = list(getattr(det, "categories", []) or [])
            if not categories:
                return 0.0
            return float(getattr(categories[0], "score", 0.0) or 0.0)

        best = max(detections, key=_score)
        box = getattr(best, "bounding_box", None)
        if box is None:
            return None

        face_x = float(getattr(box, "origin_x", 0.0))
        face_y = float(getattr(box, "origin_y", 0.0))
        face_w = float(getattr(box, "width", 0.0))
        face_h = float(getattr(box, "height", 0.0))
        if face_w <= 0 or face_h <= 0:
            return None
        logger.info(
            "Local MediaPipe Tasks selected face bounds x=%.1f y=%.1f w=%.1f h=%.1f.",
            face_x,
            face_y,
            face_w,
            face_h,
        )
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
        logger.info("Template '%s' uses centered detector mode.", template_obj.name)
        return AutoCropSuggestion(
            scale=fallback.scale,
            offset=list(fallback.offset),
            method="template-centered",
            status="centered_mode",
            status_message="Centered detector selected; face controls are not used.",
        )

    detector = _MediaPipeFaceSquareDetector(min_detection_confidence=template_obj.min_detection_confidence)
    if not detector.available:
        reason = detector.unavailable_reason()
        logger.warning("Template '%s': %s Falling back to centered crop.", template_obj.name, reason)
        return AutoCropSuggestion(
            scale=fallback.scale,
            offset=list(fallback.offset),
            method="centered",
            status="mediapipe_unavailable",
            status_message=f"{reason} Using centered fallback and ignoring face controls.",
        )
    face_bounds = detector.detect_face_bounds(source_img)
    if not face_bounds:
        logger.warning("Template '%s': no face detected; falling back to centered crop.", template_obj.name)
        return AutoCropSuggestion(
            scale=fallback.scale,
            offset=list(fallback.offset),
            method="centered",
            status="no_face_detected",
            status_message="No face was detected; using centered fallback and ignoring face controls.",
        )

    logger.info("Template '%s': face crop applied.", template_obj.name)
    return _face_bounds_to_suggestion(
        source_img,
        crop_size,
        face_bounds=face_bounds,
        face_width_ratio=template_obj.face_width_ratio,
        face_center_x_ratio=template_obj.face_center_x_ratio,
        face_bottom_y_ratio=template_obj.face_bottom_y_ratio,
        method=f"template:{template_obj.name}",
        status="face_crop_applied",
        status_message="Face detected; face controls are applied.",
    )


def suggest_button_autocrop(source_img: Any, crop_size: tuple[int, int]) -> AutoCropSuggestion:
    """Suggest scale/offset for button design; falls back to centered crop."""
    fallback = _default_centered_suggestion(source_img, crop_size)
    detector = _MediaPipeFaceSquareDetector()
    if not detector.available:
        reason = detector.unavailable_reason()
        logger.warning("%s Using centered fallback crop.", reason)
        return AutoCropSuggestion(
            scale=fallback.scale,
            offset=list(fallback.offset),
            method="centered",
            status="mediapipe_unavailable",
            status_message=f"{reason} Using centered fallback.",
        )
    face_bounds = detector.detect_face_bounds(source_img)
    if not face_bounds:
        logger.warning("No face detected by local MediaPipe Tasks; using centered fallback crop.")
        return AutoCropSuggestion(
            scale=fallback.scale,
            offset=list(fallback.offset),
            method="centered",
            status="no_face_detected",
            status_message="No face was detected; using centered fallback.",
        )
    logger.info("Local MediaPipe face crop applied using direct face placement.")
    return _face_bounds_to_suggestion(
        source_img,
        crop_size,
        face_bounds=face_bounds,
        face_width_ratio=DEFAULT_FACE_WIDTH_RATIO,
        face_center_x_ratio=DEFAULT_FACE_CENTER_X_RATIO,
        face_bottom_y_ratio=DEFAULT_FACE_BOTTOM_Y_RATIO,
        method="mediapipe-face",
        status="face_crop_applied",
        status_message="Face detected; face crop applied.",
    )
