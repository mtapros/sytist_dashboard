import unittest

from PIL import Image

from models import PrintJob
from printing_service import PRINT_ASPECT_RATIOS, PRODUCT_FOLDERS, PrintingService


def _make_image(width, height, color="red"):
    """Create a simple solid-color RGB image for testing."""
    img = Image.new("RGB", (width, height), color)
    return img


class DetectSizeKeyTests(unittest.TestCase):
    def setUp(self):
        self.service = PrintingService(config={})

    def test_detects_4x5(self):
        self.assertEqual(self.service.detect_size_key_from_text("4x5 Print"), "4x5")
        self.assertEqual(self.service.detect_size_key_from_text("4 x 5"), "4x5")
        self.assertEqual(self.service.detect_size_key_from_text("4×5"), "4x5")

    def test_detects_4x5_compact(self):
        self.assertEqual(self.service.detect_size_key_from_text("photo_4x5.jpg"), "4x5")

    def test_4x5_does_not_shadow_4x6(self):
        self.assertEqual(self.service.detect_size_key_from_text("4x6"), "4x6")

    def test_detects_existing_sizes(self):
        self.assertEqual(self.service.detect_size_key_from_text("5x7"), "5x7")
        self.assertEqual(self.service.detect_size_key_from_text("8x10"), "8x10")
        self.assertEqual(self.service.detect_size_key_from_text("wallet"), "wallet")

    def test_4x5_in_product_folders(self):
        self.assertIn("4x5", PRODUCT_FOLDERS)
        self.assertEqual(PRODUCT_FOLDERS["4x5"], "4x5")

    def test_determine_folder_4x5(self):
        self.assertEqual(self.service.determine_folder("4x5 Print"), "4x5")


class PrintAspectRatiosTests(unittest.TestCase):
    def test_ratios_defined(self):
        self.assertIn("4x6", PRINT_ASPECT_RATIOS)
        self.assertIn("4x5", PRINT_ASPECT_RATIOS)
        self.assertIn("5x7", PRINT_ASPECT_RATIOS)
        self.assertIn("8x10", PRINT_ASPECT_RATIOS)

    def test_4x6_ratio(self):
        short, long = PRINT_ASPECT_RATIOS["4x6"]
        self.assertAlmostEqual(short / long, 2 / 3, places=4)

    def test_4x5_ratio(self):
        short, long = PRINT_ASPECT_RATIOS["4x5"]
        self.assertAlmostEqual(short / long, 4 / 5, places=4)

    def test_5x7_ratio(self):
        short, long = PRINT_ASPECT_RATIOS["5x7"]
        self.assertAlmostEqual(short / long, 5 / 7, places=4)

    def test_8x10_ratio(self):
        short, long = PRINT_ASPECT_RATIOS["8x10"]
        self.assertAlmostEqual(short / long, 4 / 5, places=4)


class CenterCropTests(unittest.TestCase):
    def setUp(self):
        self.service = PrintingService(config={})

    def _assert_ratio(self, w, h, short_r, long_r):
        """Check that w/h approximates short_r/long_r (in portrait) or its inverse."""
        # Normalize to portrait comparison
        short_dim = min(w, h)
        long_dim = max(w, h)
        self.assertAlmostEqual(short_dim / long_dim, short_r / long_r, places=2)

    # --- Portrait images ---

    def test_portrait_2x3_native_crop_to_5x7(self):
        """A 2:3 portrait image cropped for 5x7 should lose top/bottom edges."""
        img = _make_image(2000, 3000)  # native 2x3
        result = self.service._center_crop_to_print_ratio(img, "5x7")
        self._assert_ratio(result.width, result.height, 5, 7)
        # Width stays the same (portrait: short side constrained)
        self.assertEqual(result.width, 2000)
        self.assertLess(result.height, 3000)

    def test_portrait_2x3_native_crop_to_8x10(self):
        """A 2:3 portrait image cropped for 8x10 should lose top/bottom edges."""
        img = _make_image(2000, 3000)
        result = self.service._center_crop_to_print_ratio(img, "8x10")
        self._assert_ratio(result.width, result.height, 4, 5)
        self.assertEqual(result.width, 2000)
        self.assertLess(result.height, 3000)

    def test_portrait_5x7_crop_to_2x3(self):
        """A 5:7 portrait image cropped for 4x6 (2:3) should lose short-edge slices."""
        img = _make_image(500, 700)
        result = self.service._center_crop_to_print_ratio(img, "4x6")
        self._assert_ratio(result.width, result.height, 2, 3)
        # Height stays; width crops
        self.assertEqual(result.height, 700)
        self.assertLess(result.width, 500)

    def test_portrait_5x7_crop_to_4x5(self):
        """A 5:7 portrait image cropped for 4:5 should lose long-edge slices."""
        img = _make_image(500, 700)
        result = self.service._center_crop_to_print_ratio(img, "4x5")
        self._assert_ratio(result.width, result.height, 4, 5)
        self.assertEqual(result.width, 500)
        self.assertLess(result.height, 700)

    def test_portrait_5x7_crop_to_8x10(self):
        """A 5:7 portrait image cropped for 8:10 (4:5) should lose long-edge slices."""
        img = _make_image(500, 700)
        result = self.service._center_crop_to_print_ratio(img, "8x10")
        self._assert_ratio(result.width, result.height, 4, 5)
        self.assertEqual(result.width, 500)
        self.assertLess(result.height, 700)

    def test_portrait_4x5_crop_to_2x3(self):
        """A 4:5 portrait image cropped for 2:3 should lose short-edge slices."""
        img = _make_image(400, 500)
        result = self.service._center_crop_to_print_ratio(img, "4x6")
        self._assert_ratio(result.width, result.height, 2, 3)
        self.assertEqual(result.height, 500)
        self.assertLess(result.width, 400)

    def test_portrait_4x5_crop_to_5x7(self):
        """A 4:5 portrait image cropped for 5:7 should lose short-edge slices."""
        img = _make_image(400, 500)
        result = self.service._center_crop_to_print_ratio(img, "5x7")
        self._assert_ratio(result.width, result.height, 5, 7)
        self.assertEqual(result.height, 500)
        self.assertLess(result.width, 400)

    # --- Landscape images ---

    def test_landscape_4x6_crop_to_5x7(self):
        """A landscape 3:2 image cropped for 5x7 (landscape 7:5) loses left/right edges."""
        img = _make_image(3000, 2000)  # landscape 3:2 (wider than 7:5)
        result = self.service._center_crop_to_print_ratio(img, "5x7")
        self._assert_ratio(result.width, result.height, 5, 7)
        # Source is wider than target ratio, so height stays and width crops
        self.assertEqual(result.height, 2000)
        self.assertLess(result.width, 3000)

    def test_landscape_5x7_crop_to_4x6(self):
        """A landscape 7:5 image cropped for 4x6 (landscape 3:2) loses top/bottom edges."""
        img = _make_image(700, 500)  # landscape 7:5 (narrower than 3:2)
        result = self.service._center_crop_to_print_ratio(img, "4x6")
        self._assert_ratio(result.width, result.height, 2, 3)
        # Source is narrower than target ratio, so width stays and height crops
        self.assertEqual(result.width, 700)
        self.assertLess(result.height, 500)

    # --- No-op cases ---

    def test_unknown_size_key_returns_unchanged(self):
        """An unknown size key (e.g. button) must not crop the image."""
        img = _make_image(300, 400)
        result = self.service._center_crop_to_print_ratio(img, "button")
        self.assertEqual(result.size, (300, 400))

    def test_none_size_key_returns_unchanged(self):
        img = _make_image(300, 400)
        result = self.service._center_crop_to_print_ratio(img, None)
        self.assertEqual(result.size, (300, 400))

    def test_already_correct_ratio_not_enlarged(self):
        """An image that already matches the target ratio should not be enlarged."""
        img = _make_image(500, 700)  # 5:7 exactly
        result = self.service._center_crop_to_print_ratio(img, "5x7")
        self.assertEqual(result.size, (500, 700))

    # --- prepare_image_for_job integration ---

    def test_prepare_image_for_job_crops_non_wallet(self):
        """_prepare_image_for_job should apply center-crop for known print sizes."""
        import io
        import tempfile
        import os

        img = _make_image(2000, 3000)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            img.save(tmp.name, format="JPEG")
            tmp_path = tmp.name

        try:
            job = PrintJob(
                source_type="file",
                source=tmp_path,
                display_name="test.jpg",
                product="5x7",
                size_key="5x7",
            )
            result = self.service._prepare_image_for_job(job)
            # Should be cropped to 5:7 ratio
            short_dim = min(result.width, result.height)
            long_dim = max(result.width, result.height)
            self.assertAlmostEqual(short_dim / long_dim, 5 / 7, places=2)
        finally:
            os.unlink(tmp_path)

    def test_prepare_image_for_job_wallet_unchanged_flow(self):
        """_prepare_image_for_job with wallet key still builds a wallet sheet."""
        import io
        import tempfile
        import os

        img = _make_image(500, 700)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            img.save(tmp.name, format="JPEG")
            tmp_path = tmp.name

        try:
            job = PrintJob(
                source_type="file",
                source=tmp_path,
                display_name="wallet.jpg",
                product="wallet",
                size_key="wallet",
            )
            result = self.service._prepare_image_for_job(job)
            # Wallet sheet is 1500x2100
            self.assertEqual(result.size, (1500, 2100))
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
