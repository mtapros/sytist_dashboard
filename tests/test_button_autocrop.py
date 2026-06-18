from __future__ import annotations

import unittest
from unittest import mock

from button_autocrop import (
    AutoCropSuggestion,
    AutoCropTemplate,
    format_autocrop_status_line,
    normalize_autocrop_template_store,
    suggest_button_autocrop,
    suggest_button_autocrop_from_template,
)


class _DummyImage:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.size = (width, height)

    def convert(self, _mode: str) -> "_DummyImage":
        return self


class ButtonAutoCropTests(unittest.TestCase):
    def test_falls_back_to_centered_when_no_detection(self):
        image = _DummyImage(3000, 2000)
        with mock.patch(
            "button_autocrop._MediaPipeFaceSquareDetector.available",
            new_callable=mock.PropertyMock,
            return_value=True,
        ), mock.patch("button_autocrop._MediaPipeFaceSquareDetector.detect_square", return_value=None):
            suggestion = suggest_button_autocrop(image, (1200, 1200))
        self.assertEqual(suggestion.method, "centered")
        self.assertEqual(suggestion.status, "no_face_detected")
        self.assertEqual(suggestion.offset, [-300, 0])

    def test_uses_mediapipe_square_when_detection_available(self):
        image = _DummyImage(3000, 3000)
        with mock.patch(
            "button_autocrop._MediaPipeFaceSquareDetector.available",
            new_callable=mock.PropertyMock,
            return_value=True,
        ), mock.patch(
            "button_autocrop._MediaPipeFaceSquareDetector.detect_square",
            return_value=(600.0, 600.0, 1000.0),
        ):
            suggestion = suggest_button_autocrop(image, (1200, 1200))
        self.assertEqual(suggestion.method, "mediapipe-face")
        self.assertEqual(suggestion.status, "face_crop_applied")
        self.assertEqual(suggestion.offset, [-720, -720])
        self.assertAlmostEqual(suggestion.scale, 1.2)

    def test_template_autocrop_uses_face_bounds_buffers_and_anchor(self):
        image = _DummyImage(3000, 3000)
        template = AutoCropTemplate(
            name="Headroom",
            top_buffer=0.2,
            bottom_buffer=0.4,
            left_buffer=0.1,
            right_buffer=0.1,
            scale_multiplier=1.25,
            anchor_x=0.5,
            anchor_y=0.4,
        )
        with mock.patch(
            "button_autocrop._MediaPipeFaceSquareDetector.available",
            new_callable=mock.PropertyMock,
            return_value=True,
        ), mock.patch(
            "button_autocrop._MediaPipeFaceSquareDetector.detect_face_bounds",
            return_value=(1000.0, 800.0, 500.0, 600.0),
        ):
            suggestion = suggest_button_autocrop_from_template(image, (1200, 1200), template)
        self.assertEqual(suggestion.method, "template:Headroom")
        self.assertEqual(suggestion.status, "face_crop_applied")
        self.assertEqual(suggestion.status_message, "Face detected; face controls are applied.")
        self.assertEqual(suggestion.face_bounds, (1000.0, 800.0, 500.0, 600.0))
        self.assertAlmostEqual(suggestion.scale, 1.0)
        self.assertEqual(suggestion.offset, [-650, -464])

    def test_template_autocrop_falls_back_to_centered_when_no_face_detected(self):
        image = _DummyImage(3000, 2000)
        template = AutoCropTemplate(name="Fallback Test")
        with mock.patch(
            "button_autocrop._MediaPipeFaceSquareDetector.available",
            new_callable=mock.PropertyMock,
            return_value=True,
        ), mock.patch("button_autocrop._MediaPipeFaceSquareDetector.detect_face_bounds", return_value=None):
            suggestion = suggest_button_autocrop_from_template(image, (1200, 1200), template)
        self.assertEqual(suggestion.method, "centered")
        self.assertEqual(suggestion.status, "no_face_detected")
        self.assertIn("No face was detected", suggestion.status_message)
        self.assertEqual(suggestion.offset, [-300, 0])

    def test_template_autocrop_reports_centered_mode(self):
        image = _DummyImage(3000, 2000)
        template = AutoCropTemplate(name="Centered", detector_mode="centered")
        suggestion = suggest_button_autocrop_from_template(image, (1200, 1200), template)
        self.assertEqual(suggestion.method, "template-centered")
        self.assertEqual(suggestion.status, "centered_mode")
        self.assertIn("Centered detector selected", suggestion.status_message)
        self.assertEqual(suggestion.offset, [-300, 0])

    def test_template_autocrop_reports_mediapipe_unavailable(self):
        image = _DummyImage(3000, 2000)
        template = AutoCropTemplate(name="Fallback Test")
        with mock.patch(
            "button_autocrop._MediaPipeFaceSquareDetector.available",
            new_callable=mock.PropertyMock,
            return_value=False,
        ):
            suggestion = suggest_button_autocrop_from_template(image, (1200, 1200), template)
        self.assertEqual(suggestion.method, "centered")
        self.assertEqual(suggestion.status, "mediapipe_unavailable")
        self.assertIn("Using centered fallback", suggestion.status_message)
        self.assertEqual(suggestion.offset, [-300, 0])

    def test_status_line_formats_key_autocrop_outcomes(self):
        centered = format_autocrop_status_line(
            AutoCropSuggestion(scale=1.0, offset=[0, 0], method="centered", status="centered_mode", status_message="Centered detector selected.")
        )
        unavailable = format_autocrop_status_line(
            AutoCropSuggestion(scale=1.0, offset=[0, 0], method="centered", status="mediapipe_unavailable", status_message="MediaPipe missing.")
        )
        no_face = format_autocrop_status_line(
            AutoCropSuggestion(scale=1.0, offset=[0, 0], method="centered", status="no_face_detected", status_message="No face found.")
        )
        applied = format_autocrop_status_line(
            AutoCropSuggestion(scale=1.0, offset=[0, 0], method="mediapipe-face", status="face_crop_applied", status_message="Face crop applied.")
        )
        self.assertTrue(centered.startswith("Centered mode selected:"))
        self.assertTrue(unavailable.startswith("MediaPipe unavailable:"))
        self.assertTrue(no_face.startswith("No face detected:"))
        self.assertTrue(applied.startswith("Face crop applied:"))

    def test_template_autocrop_logs_fallback_when_no_face_detected(self):
        image = _DummyImage(3000, 2000)
        template = AutoCropTemplate(name="Fallback Test")
        with mock.patch(
            "button_autocrop._MediaPipeFaceSquareDetector.available",
            new_callable=mock.PropertyMock,
            return_value=True,
        ), mock.patch("button_autocrop._MediaPipeFaceSquareDetector.detect_face_bounds", return_value=None), mock.patch(
            "button_autocrop.logger"
        ) as logger_mock:
            suggest_button_autocrop_from_template(image, (1200, 1200), template)
        logger_mock.warning.assert_any_call("Template '%s': no face detected; falling back to centered crop.", template.name)

    def test_normalize_template_store_adds_default_and_clamps_values(self):
        normalized = normalize_autocrop_template_store(
            {
                "selected_template": "Custom",
                "templates": {
                    "Custom": {
                        "name": "Custom",
                        "top_buffer": "-1",
                        "bottom_buffer": "0.75",
                        "left_buffer": "9",
                        "right_buffer": "0.25",
                        "scale_multiplier": "0",
                        "anchor_x": "2",
                        "anchor_y": "-1",
                        "min_detection_confidence": "0.01",
                    }
                },
            }
        )
        custom = normalized["templates"]["Custom"]
        self.assertEqual(normalized["selected_template"], "Custom")
        self.assertEqual(custom["top_buffer"], 0.0)
        self.assertEqual(custom["left_buffer"], 5.0)
        self.assertEqual(custom["scale_multiplier"], 0.25)
        self.assertEqual(custom["anchor_x"], 1.0)
        self.assertEqual(custom["anchor_y"], 0.0)
        self.assertEqual(custom["min_detection_confidence"], 0.05)

    def test_template_persists_detection_confidence(self):
        template = AutoCropTemplate.from_dict(
            {
                "name": "Loose Detector",
                "min_detection_confidence": "0.25",
            }
        )
        self.assertEqual(template.min_detection_confidence, 0.25)
        self.assertEqual(template.to_dict()["min_detection_confidence"], 0.25)


if __name__ == "__main__":
    unittest.main()
