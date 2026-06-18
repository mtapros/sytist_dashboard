from __future__ import annotations

import unittest

from button_autocrop import suggest_button_autocrop


class _FakeImage:
    def __init__(self, w: int, h: int) -> None:
        self.size = (w, h)


class ButtonAutoCropTests(unittest.TestCase):
    def test_returns_scale_and_offset_when_detector_finds_bbox(self):
        img = _FakeImage(2000, 1500)
        result = suggest_button_autocrop(
            img,
            crop_size=(1200, 1200),
            detector=lambda _img: (0.30, 0.20, 0.35, 0.35),
        )
        self.assertIsNotNone(result)
        self.assertGreater(result["scale"], 0.0)
        self.assertEqual(len(result["offset"]), 2)

    def test_returns_none_when_no_detection(self):
        img = _FakeImage(2000, 1500)
        result = suggest_button_autocrop(
            img,
            crop_size=(1200, 1200),
            detector=lambda _img: None,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
