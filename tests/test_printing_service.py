import unittest
import tempfile
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

    def test_address_lines_use_custom_text_when_provided(self):
        lines = self.service._address_lines_for_label(
            ShippingAddress(
                full_name="Jane Doe",
                address_1="123 Main St",
                city="Albany",
                state="NY",
                postal_code="12207",
                custom_text="Line One\nLine Two",
            )
        )
        self.assertEqual(lines, ["Line One", "Line Two"])

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

    def test_render_address_label_supports_custom_text_without_street_city_state_zip(self):
        img = self.service._render_address_label(
            ShippingAddress(
                custom_text="Custom Label\nSecond Line",
            )
        )
        self.assertEqual(img.size, ADDRESS_LABEL_SIZE)
        diff = ImageChops.difference(img, Image.new("RGB", img.size, "white"))
        self.assertIsNotNone(diff.getbbox())

    def test_render_address_label_draws_logo_with_position_and_scale(self):
        logo = Image.new("RGBA", (20, 20), (0, 0, 0, 255))
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            logo.save(tmp.name, format="PNG")
            img = self.service._render_address_label(
                ShippingAddress(custom_text="Custom"),
                label_options={
                    "logo_path": tmp.name,
                    "logo_scale": 2.0,
                    "logo_x": 50,
                    "logo_y": 60,
                },
            )
        self.assertEqual(img.getpixel((55, 65)), (0, 0, 0))

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

    # --- circle_offset (D-pad) tests ---

    def test_circle_offset_shifts_circle_right(self):
        """A positive x offset moves the circle right; the right edge becomes blue."""
        img = _make_image(1200, 1200, "blue")
        # Use a smaller circle so there's white space on both sides by default.
        # With diameter=800, circle occupies x=[200,999] (centered in 1200).
        # With offset +200, circle occupies x=[400,1199]; right edge (x=1199) gains blue.
        # Without offset, right edge pixel on sheet at x=1199, y=900 is outside the circle (white).
        result_centered = self.service.render_button_sheet(img, circle_diameter=800)
        result_offset = self.service.render_button_sheet(img, circle_diameter=800, circle_offset=(200, 0))

        # With no offset, sheet pixel at x=1190 is outside the circle (white area).
        self.assertEqual(result_centered.getpixel((1190, 900)), (255, 255, 255))
        # With +200 offset, that same pixel is now inside the circle (blue).
        self.assertEqual(result_offset.getpixel((1190, 900)), (0, 0, 255))

    def test_circle_offset_zero_matches_default(self):
        """Explicit (0, 0) offset produces same result as no offset."""
        img = _make_image(1200, 1200, "green")
        result_default = self.service.render_button_sheet(img)
        result_zero = self.service.render_button_sheet(img, circle_offset=(0, 0))
        diff = ImageChops.difference(result_default, result_zero)
        self.assertIsNone(diff.getbbox())

    def test_circle_offset_clamped_to_keep_circle_in_bounds(self):
        """An extreme offset is clamped so the circle stays within the crop."""
        img = _make_image(1200, 1200, "red")
        # Offset larger than maximum should not crash and return a valid sheet.
        result = self.service.render_button_sheet(img, circle_diameter=800, circle_offset=(9999, 9999))
        self.assertEqual(result.size, BUTTON_PRINT_SIZE)

    def test_red_circle_locked_to_main_circle_offset(self):
        """The red finished circle shifts with the main circle offset."""
        img = _make_image(1200, 1200, "white")
        # With a large downward offset the red circle should appear in the lower half.
        result = self.service.render_button_sheet(
            img,
            circle_diameter=1200,
            finished_diameter=300,
            print_finished_circle=True,
            circle_offset=(0, 200),
        )
        # Top of the sheet center should be white (red circle has moved down).
        top_pixel = result.getpixel((600, 320))
        self.assertEqual(top_pixel, (255, 255, 255))

    # --- edge_border tests ---

    def test_edge_border_off_by_default(self):
        """Without edge_border=True the yellow border is not drawn."""
        img = _make_image(1200, 1200, "white")
        result = self.service.render_button_sheet(img)
        # The very center-top of the circle edge should be white (no yellow).
        # BUTTON_CROP_SIZE 1200x1200 is centered at sheet y=300..1500.
        # Circle edge top on sheet is at y=300; center x=600.
        pixel = result.getpixel((600, 300))
        self.assertEqual(pixel, (255, 255, 255))

    def test_edge_border_draws_yellow_outline(self):
        """edge_border=True produces a yellow pixel on the circle edge."""
        img = _make_image(1200, 1200, "white")
        result = self.service.render_button_sheet(img, edge_border=True)
        # The very top of the circle (center x, top y on sheet) should contain yellow.
        # Circle top on sheet is at y=300 (since (1800-1200)//2 = 300).
        # We check a band of pixels near the top of the circle for any yellow.
        found_yellow = False
        for y in range(300, 310):
            for x in range(595, 606):
                r, g, b = result.getpixel((x, y))
                if r > 200 and g > 200 and b < 50:
                    found_yellow = True
                    break
        self.assertTrue(found_yellow, "Expected yellow border pixels near circle top edge")

    # --- text stroke tests ---

    def test_text_stroke_produces_different_result_than_no_stroke(self):
        """Stroke config changes the rendered output."""
        img = _make_image(1200, 1200, "white")
        base = self.service.render_button_sheet(
            img,
            curved_text={
                "text": "HI",
                "position": "top",
                "inward": False,
                "font_family": "DejaVuSans.ttf",
                "font_size": 72,
                "color": "#000000",
                "style": "Regular",
                "char_spacing": 0,
                "stroke_color": "",
                "stroke_width": 0,
            },
        )
        stroked = self.service.render_button_sheet(
            img,
            curved_text={
                "text": "HI",
                "position": "top",
                "inward": False,
                "font_family": "DejaVuSans.ttf",
                "font_size": 72,
                "color": "#000000",
                "style": "Regular",
                "char_spacing": 0,
                "stroke_color": "#ff0000",
                "stroke_width": 4,
            },
        )
        diff = ImageChops.difference(base, stroked)
        self.assertIsNotNone(diff.getbbox(), "Stroke should produce a different image")

    def test_text_stroke_zero_width_matches_no_stroke(self):
        """stroke_width=0 should produce the same result regardless of stroke_color."""
        img = _make_image(1200, 1200, "white")
        text_cfg_base = {
            "text": "A",
            "position": "top",
            "inward": False,
            "font_family": "DejaVuSans.ttf",
            "font_size": 60,
            "color": "#000000",
            "style": "Regular",
            "char_spacing": 0,
            "stroke_width": 0,
        }
        text_cfg_with_color = dict(text_cfg_base, stroke_color="#ff0000")
        result_base = self.service.render_button_sheet(img, curved_text=text_cfg_base)
        result_color = self.service.render_button_sheet(img, curved_text=text_cfg_with_color)
        diff = ImageChops.difference(result_base, result_color)
        self.assertIsNone(diff.getbbox(), "stroke_width=0 should ignore stroke_color")

    # --- print_params tests ---

    def test_print_params_produces_non_blank_footer(self):
        """print_params=True renders text in the bottom margin of the sheet."""
        img = _make_image(1200, 1200, "white")
        result_no_params = self.service.render_button_sheet(img)
        result_params = self.service.render_button_sheet(img, print_params=True)
        # The bottom margin area should differ when params are printed.
        sheet_h = BUTTON_PRINT_SIZE[1]
        crop_end = (sheet_h + BUTTON_CROP_SIZE[1]) // 2  # bottom of crop area
        diff = ImageChops.difference(result_no_params, result_params)
        bbox = diff.getbbox()
        self.assertIsNotNone(bbox, "print_params should add content to the sheet")
        self.assertGreaterEqual(bbox[1], crop_end - 5, "Params footer should be below the crop area")


if __name__ == "__main__":
    unittest.main()
