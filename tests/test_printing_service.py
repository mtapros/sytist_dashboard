import unittest
from unittest import mock

from PIL import Image, ImageChops

from models import PrintJob, ShippingAddress
from printing_service import (
    ADDRESS_LABEL_SIZE,
    ADDRESS_LABEL_TEXT_WIDTH_RATIO,
    BUTTON_CROP_SIZE,
    BUTTON_DEFAULT_DIAMETER,
    BUTTON_PRINT_SIZE,
    PRINT_ASPECT_RATIOS,
    PRODUCT_FOLDERS,
    PrintingService,
)


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


class AddressLabelTests(unittest.TestCase):
    def setUp(self):
        self.service = PrintingService(config={})

    def test_address_lines_omit_us_country(self):
        lines = self.service._address_lines_for_label(
            ShippingAddress(
                full_name="Jane Doe",
                address_1="123 Main St",
                city="Albany",
                state="NY",
                postal_code="12207",
                country="US",
            )
        )
        self.assertEqual(lines, ["Jane Doe", "123 Main St", "Albany, NY 12207"])

    def test_address_lines_include_non_us_country(self):
        lines = self.service._address_lines_for_label(
            ShippingAddress(
                full_name="Jane Doe",
                address_1="123 Main St",
                city="Toronto",
                state="ON",
                postal_code="M5V 2T6",
                country="Canada",
            )
        )
        self.assertEqual(lines[-1], "Canada")

    def test_render_address_label_creates_centered_4x6_canvas(self):
        img = self.service._render_address_label(
            ShippingAddress(
                full_name="Jane Doe",
                address_1="123 Main Street",
                address_2="Suite 4B",
                city="Albany",
                state="NY",
                postal_code="12207",
                country="US",
            )
        )

        self.assertEqual(img.size, ADDRESS_LABEL_SIZE)
        self.assertEqual(round(img.size[0] * ADDRESS_LABEL_TEXT_WIDTH_RATIO), 1080)
        diff = ImageChops.difference(img, Image.new("RGB", img.size, "white"))
        self.assertIsNotNone(diff.getbbox())

    def test_prepare_image_for_address_job_uses_label_renderer(self):
        job = PrintJob(
            source_type="address",
            source={},
            display_name="Jane Doe",
            product="4x6 Address Label",
            size_key="4x6",
            address=ShippingAddress(
                full_name="Jane Doe",
                address_1="123 Main Street",
                city="Albany",
                state="NY",
                postal_code="12207",
                country="US",
            ),
        )

        result = self.service._prepare_image_for_job(job)
        self.assertEqual(result.size, ADDRESS_LABEL_SIZE)


class ButtonSheetTests(unittest.TestCase):
    def setUp(self):
        self.service = PrintingService(config={})

    def test_render_button_sheet_creates_centered_4x6_canvas(self):
        img = _make_image(500, 500, "red")
        result = self.service.render_button_sheet(img)

        self.assertEqual(result.size, BUTTON_PRINT_SIZE)
        self.assertEqual(BUTTON_CROP_SIZE, (1200, 1200))
        self.assertEqual(BUTTON_DEFAULT_DIAMETER, 1200)
        self.assertEqual(result.getpixel((600, 900)), (255, 0, 0))
        self.assertEqual(result.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(result.getpixel((0, 300)), (255, 255, 255))

    def test_render_button_sheet_keeps_circle_only_over_white_page(self):
        img = _make_image(1000, 500, "blue")
        result = self.service.render_button_sheet(img, scale=2.4, offset=(-600, 0))

        self.assertEqual(result.getpixel((600, 900)), (0, 0, 255))
        self.assertEqual(result.getpixel((10, 310)), (255, 255, 255))
        self.assertEqual(result.getpixel((1190, 310)), (255, 255, 255))

    def test_prepare_image_for_pil_job_returns_rgb_image(self):
        img = Image.new("RGBA", (20, 20), (255, 0, 0, 128))
        job = PrintJob(
            source_type="pil",
            source=img,
            display_name="button.png",
            product="Button",
            size_key="button",
        )

        result = self.service._prepare_image_for_job(job)
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.size, (20, 20))

    def test_render_button_sheet_allows_smaller_outer_diameter(self):
        img = _make_image(1200, 1200, "blue")
        result = self.service.render_button_sheet(img, circle_diameter=800)

        self.assertEqual(result.getpixel((600, 900)), (0, 0, 255))
        self.assertEqual(result.getpixel((199, 900)), (255, 255, 255))
        self.assertEqual(result.getpixel((1001, 900)), (255, 255, 255))

    def test_render_button_sheet_can_print_finished_red_circle(self):
        img = _make_image(1200, 1200, "white")
        result = self.service.render_button_sheet(
            img,
            finished_diameter=600,
            print_finished_circle=True,
        )

        self.assertEqual(result.getpixel((600, 600)), (255, 0, 0))
        self.assertEqual(result.getpixel((600, 900)), (255, 255, 255))

    def test_render_button_sheet_can_add_curved_text(self):
        img = _make_image(1200, 1200, "white")
        result = self.service.render_button_sheet(
            img,
            curved_text={
                "text": "BUTTON",
                "position": "top",
                "inward": False,
                "font_family": "DejaVuSans.ttf",
                "font_size": 72,
                "color": "#000000",
                "style": "Regular",
                "char_spacing": 4,
            },
        )

        diff = ImageChops.difference(result, Image.new("RGB", result.size, "white"))
        self.assertIsNotNone(diff.getbbox())

    def test_curved_text_bottom_outward_preserves_typed_order(self):
        img = _make_image(1200, 1200, "white")
        x_positions = []
        original_paste = Image.Image.paste

        def recording_paste(self_img, im, box=None, mask=None):
            if mask is not None and isinstance(box, tuple) and len(box) == 2:
                x_positions.append(box[0])
            return original_paste(self_img, im, box=box, mask=mask)

        with mock.patch.object(Image.Image, "paste", new=recording_paste):
            self.service._draw_curved_button_text(
                img,
                {
                    "text": "12",
                    "position": "bottom",
                    "inward": False,
                    "font_family": "DejaVuSans.ttf",
                    "font_size": 72,
                    "color": "#000000",
                    "style": "Regular",
                    "char_spacing": 4,
                },
                (0, 0, 1199, 1199),
            )

        self.assertEqual(len(x_positions), 2)
        self.assertLess(x_positions[0], x_positions[1])

    def test_curved_text_bottom_outward_center_char_uses_bottom_tangent(self):
        img = _make_image(1200, 1200, "white")
        rotations = []
        original_rotate = Image.Image.rotate

        def recording_rotate(self_img, angle, *args, **kwargs):
            rotations.append(angle)
            return original_rotate(self_img, angle, *args, **kwargs)

        with mock.patch.object(Image.Image, "rotate", new=recording_rotate):
            self.service._draw_curved_button_text(
                img,
                {
                    "text": "2",
                    "position": "bottom",
                    "inward": False,
                    "font_family": "DejaVuSans.ttf",
                    "font_size": 72,
                    "color": "#000000",
                    "style": "Regular",
                    "char_spacing": 0,
                },
                (0, 0, 1199, 1199),
            )

        self.assertEqual(len(rotations), 1)
        normalized = rotations[0] % 360
        self.assertAlmostEqual(normalized, 180, delta=2)

    def test_curved_text_bottom_outward_leans_along_bottom_arc(self):
        img = _make_image(1200, 1200, "white")
        rotations = []
        original_rotate = Image.Image.rotate

        def recording_rotate(self_img, angle, *args, **kwargs):
            rotations.append(angle)
            return original_rotate(self_img, angle, *args, **kwargs)

        with mock.patch.object(Image.Image, "rotate", new=recording_rotate):
            self.service._draw_curved_button_text(
                img,
                {
                    "text": "2026",
                    "position": "bottom",
                    "inward": False,
                    "font_family": "DejaVuSans.ttf",
                    "font_size": 72,
                    "color": "#000000",
                    "style": "Regular",
                    "char_spacing": 4,
                },
                (0, 0, 1199, 1199),
            )

        self.assertEqual(len(rotations), 4)
        normalized = [angle % 360 for angle in rotations]
        self.assertGreater(normalized[0], 180)
        self.assertGreater(normalized[1], 180)
        self.assertLess(normalized[2], 180)
        self.assertLess(normalized[3], 180)

    def test_render_button_sheet_can_add_lime_calibration_rectangle(self):
        img = _make_image(1200, 1200, "white")
        result = self.service.render_button_sheet(
            img,
            print_lime_calibration_rectangle=True,
            lime_rectangle_width=1200,
        )

        self.assertEqual(result.getpixel((600, 0)), (0, 255, 0))
        self.assertEqual(result.getpixel((0, 900)), (0, 255, 0))

    def test_render_button_sheet_lime_rectangle_respects_width(self):
        img = _make_image(1200, 1200, "white")
        result = self.service.render_button_sheet(
            img,
            print_lime_calibration_rectangle=True,
            lime_rectangle_width=800,
        )

        self.assertEqual(result.getpixel((600, 300)), (0, 255, 0))
        self.assertEqual(result.getpixel((600, 0)), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
