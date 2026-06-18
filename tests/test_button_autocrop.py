from __future__ import annotations

import unittest
from unittest import mock

from button_autocrop import suggest_button_autocrop


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
        with mock.patch("button_autocrop._MediaPipeFaceSquareDetector.detect_square", return_value=None):
            suggestion = suggest_button_autocrop(image, (1200, 1200))
        self.assertEqual(suggestion.method, "centered")
        self.assertEqual(suggestion.offset, [-300, 0])

    def test_uses_mediapipe_square_when_detection_available(self):
        image = _DummyImage(3000, 3000)
        with mock.patch(
            "button_autocrop._MediaPipeFaceSquareDetector.detect_square",
            return_value=(600.0, 600.0, 1000.0),
        ):
            suggestion = suggest_button_autocrop(image, (1200, 1200))
        self.assertEqual(suggestion.method, "mediapipe-face")
        self.assertEqual(suggestion.offset, [-720, -720])
        self.assertAlmostEqual(suggestion.scale, 1.2)


if __name__ == "__main__":
    unittest.main()
