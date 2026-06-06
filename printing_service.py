import io
import math
import os
import re
import urllib.request

try:
    from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps, ImageWin
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None
    ImageColor = None
    ImageDraw = None
    ImageFont = None
    ImageOps = None
    ImageWin = None

try:
    import win32con
    import win32print
    import win32ui
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    win32con = None
    win32print = None
    win32ui = None

from models import CartItem, PrintJob, ShippingAddress

PRODUCT_FOLDERS = {
    "5x7": "5x7",
    "4x6": "4x6",
    "4x5": "4x5",
    "8x10": "8x10",
    "wallet": "Wallet",
    "button": "Button",
    "magnet": "Magnet",
    "7in": "7inStatuette",
    "10in": "10inStatuette",
}

# Maps size keys to (short_side, long_side) aspect ratio tuples.
# 4x6 uses the 2x3 ratio (same proportions). 4x5 and 8x10 share the same ratio.
PRINT_ASPECT_RATIOS = {
    "4x6": (2, 3),
    "4x5": (4, 5),
    "5x7": (5, 7),
    "8x10": (4, 5),
}

ADDRESS_LABEL_SIZE = (1800, 1200)
ADDRESS_LABEL_TEXT_WIDTH_RATIO = 0.60
ADDRESS_LABEL_TEXT_HEIGHT_RATIO = 0.70
ADDRESS_LABEL_LINE_SPACING_RATIO = 0.22
ADDRESS_LABEL_FONT_MAX = 140
ADDRESS_LABEL_FONT_MIN = 36
BUTTON_PRINT_SIZE = (1200, 1800)
BUTTON_CROP_SIZE = (1200, 1200)
BUTTON_DEFAULT_DIAMETER = 1200
BUTTON_DEFAULT_FINISHED_DIAMETER = 900


class PrintingService:
    def __init__(self, config: dict):
        self.config = config

    def get_installed_printers(self):
        if not HAS_WIN32:
            return []
        return sorted([
            p[2] for p in win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            )
        ])

    def determine_folder(self, product_name):
        name_lower = (product_name or "").lower()
        for key, folder in PRODUCT_FOLDERS.items():
            if key in name_lower:
                return folder
        return "Other_Prints"

    def detect_size_key_from_text(self, text):
        t = (text or "").lower().strip()
        if not t:
            return None

        patterns = [
            (r'\b4\s*[x×]\s*6\b', "4x6"),
            (r'\b4\s*[x×]\s*5\b', "4x5"),
            (r'\b5\s*[x×]\s*7\b', "5x7"),
            (r'\b8\s*[x×]\s*10\b', "8x10"),
            (r'\b7\s*(?:in|inch)?\s*statuette\b', "7in"),
            (r'\b10\s*(?:in|inch)?\s*statuette\b', "10in"),
            (r'\bwallets?\b', "wallet"),
            (r'\bbuttons?\b', "button"),
            (r'\bmagnets?\b', "magnet"),
        ]
        for pattern, size_key in patterns:
            if re.search(pattern, t, flags=re.IGNORECASE):
                return size_key

        compact = t.replace(" ", "")
        if "4x6" in compact:
            return "4x6"
        if "4x5" in compact:
            return "4x5"
        if "5x7" in compact:
            return "5x7"
        if "8x10" in compact:
            return "8x10"
        if "wallet" in compact:
            return "wallet"
        if "button" in compact:
            return "button"
        if "magnet" in compact:
            return "magnet"
        if "7instatuette" in compact or "7inchstatuette" in compact:
            return "7in"
        if "10instatuette" in compact or "10inchstatuette" in compact:
            return "10in"
        return None

    def detect_size_key_for_order_item(self, item: CartItem):
        return self.detect_size_key_from_text(item.product) or self.detect_size_key_from_text(item.file)

    def detect_size_key_for_filepath(self, filepath):
        return self.detect_size_key_from_text(os.path.basename(filepath))

    def get_routed_printer_for_key(self, size_key):
        routes = self.config.get("printer_routes", {})
        if not size_key:
            return None
        printer = (routes.get(size_key) or "").strip()
        if printer:
            return printer
        if size_key == "wallet":
            printer = (routes.get("5x7") or "").strip()
            return printer or None
        return None

    def analyze_jobs_for_routing(self, jobs):
        unresolved = []
        resolved_count = 0
        for job in jobs:
            routed = job.routed_printer or self.get_routed_printer_for_key(job.size_key)
            if routed:
                job.routed_printer = routed
                resolved_count += 1
            else:
                unresolved.append(job)
        return len(unresolved) == 0, unresolved, resolved_count

    def _load_image_for_job(self, job: PrintJob):
        if not HAS_PIL:
            raise RuntimeError("Please run: pip install pillow")
        if job.source_type == "url":
            req = urllib.request.Request(job.source, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                img_data = response.read()
            img = Image.open(io.BytesIO(img_data))
        else:
            img = Image.open(job.source)

        if img.mode != 'RGB':
            img = img.convert('RGB')
        return img

    @staticmethod
    def _address_lines_for_label(address: ShippingAddress):
        if not address:
            return []

        lines = []
        for value in [address.full_name, address.address_1, address.address_2]:
            text = str(value or "").strip()
            if text:
                lines.append(text)

        city = str(address.city or "").strip()
        state = str(address.state or "").strip()
        postal_code = str(address.postal_code or "").strip()
        locality = ""
        if city and state:
            locality = f"{city}, {state}"
        else:
            locality = city or state
        if postal_code:
            locality = f"{locality} {postal_code}".strip()
        if locality:
            lines.append(locality)

        country = str(address.country or "").strip()
        if country and country.upper() not in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
            lines.append(country)
        return lines

    def _load_address_label_font(self, size):
        for font_name in ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf"):
            try:
                return ImageFont.truetype(font_name, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _measure_text(draw, text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    def _wrap_text_for_width(self, draw, text, font, max_width):
        words = text.split()
        if not words:
            return [""]

        lines = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            width, _ = self._measure_text(draw, candidate, font)
            if width <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _render_address_label(self, address: ShippingAddress):
        if not HAS_PIL:
            raise RuntimeError("Please run: pip install pillow")

        lines = self._address_lines_for_label(address)
        if not lines:
            raise ValueError("Address label requires at least one address line")

        canvas_w, canvas_h = ADDRESS_LABEL_SIZE
        text_block_w = round(canvas_w * ADDRESS_LABEL_TEXT_WIDTH_RATIO)
        max_text_h = round(canvas_h * ADDRESS_LABEL_TEXT_HEIGHT_RATIO)
        img = Image.new("RGB", ADDRESS_LABEL_SIZE, "white")
        draw = ImageDraw.Draw(img)

        selected_font = None
        selected_lines = []
        selected_spacing = 0
        for size in range(ADDRESS_LABEL_FONT_MAX, ADDRESS_LABEL_FONT_MIN - 1, -4):
            font = self._load_address_label_font(size)
            spacing = max(8, int(size * ADDRESS_LABEL_LINE_SPACING_RATIO))
            wrapped_lines = []
            for line in lines:
                wrapped_lines.extend(self._wrap_text_for_width(draw, line, font, text_block_w))

            heights = [self._measure_text(draw, line, font)[1] for line in wrapped_lines]
            total_height = sum(heights) + spacing * max(0, len(wrapped_lines) - 1)
            widest_line = max((self._measure_text(draw, line, font)[0] for line in wrapped_lines), default=0)
            if total_height <= max_text_h and widest_line <= text_block_w:
                selected_font = font
                selected_lines = wrapped_lines
                selected_spacing = spacing
                break

        if selected_font is None:
            selected_font = self._load_address_label_font(ADDRESS_LABEL_FONT_MIN)
            selected_spacing = max(8, int(ADDRESS_LABEL_FONT_MIN * ADDRESS_LABEL_LINE_SPACING_RATIO))
            for line in lines:
                selected_lines.extend(self._wrap_text_for_width(draw, line, selected_font, text_block_w))

        heights = [self._measure_text(draw, line, selected_font)[1] for line in selected_lines]
        total_height = sum(heights) + selected_spacing * max(0, len(selected_lines) - 1)
        y = max(0, (canvas_h - total_height) // 2)
        text_block_x = (canvas_w - text_block_w) // 2

        for idx, line in enumerate(selected_lines):
            width, height = self._measure_text(draw, line, selected_font)
            x = text_block_x + max(0, (text_block_w - width) // 2)
            draw.text((x, y), line, fill="black", font=selected_font)
            y += height
            if idx < len(selected_lines) - 1:
                y += selected_spacing
        return img

    def _build_wallet_sheet(self, img):
        sheet_w, sheet_h = 1500, 2100
        tile_w, tile_h = sheet_w // 2, sheet_h // 2

        tile = ImageOps.fit(
            img,
            (tile_w, tile_h),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

        sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
        positions = [
            (0, 0),
            (tile_w, 0),
            (0, tile_h),
            (tile_w, tile_h),
        ]
        for pos in positions:
            sheet.paste(tile, pos)
        return sheet

    @staticmethod
    def _clamp_button_diameter(value, default=BUTTON_DEFAULT_DIAMETER):
        try:
            diameter = int(round(float(value)))
        except (TypeError, ValueError):
            diameter = default
        return max(50, min(diameter, min(BUTTON_CROP_SIZE)))

    @staticmethod
    def _clamp_lime_rect_width(value, sheet_size, default=BUTTON_PRINT_SIZE[0]):
        sheet_w, sheet_h = sheet_size
        try:
            width = int(round(float(value)))
        except (TypeError, ValueError):
            width = default
        width = max(1, min(width, sheet_w))
        max_width_by_height = int(sheet_h * 2 / 3)
        width = min(width, max_width_by_height)
        return max(1, width)

    def _draw_lime_calibration_rectangle(self, img, width):
        sheet_w, sheet_h = img.size
        rect_w = self._clamp_lime_rect_width(width, (sheet_w, sheet_h))
        rect_h = max(1, int(round(rect_w * 3 / 2)))
        left = (sheet_w - rect_w) // 2
        top = (sheet_h - rect_h) // 2
        right = left + rect_w - 1
        bottom = top + rect_h - 1
        ImageDraw.Draw(img).rectangle((left, top, right, bottom), outline="lime", width=3)

    def _load_button_font(self, font_family, size, style="Regular"):
        size = max(1, int(round(size or 1)))
        style = (style or "Regular").lower()
        family = (font_family or "").strip()
        candidates = []
        if family:
            candidates.append(family)
            base, ext = os.path.splitext(family)
            if "bold" in style and "italic" in style:
                candidates.extend([f"{base}-BoldOblique{ext}", f"{base}-BoldItalic{ext}", f"{base}bd{ext}"])
            elif "bold" in style:
                candidates.extend([f"{base}-Bold{ext}", f"{base}bd{ext}"])
            elif "italic" in style:
                candidates.extend([f"{base}-Oblique{ext}", f"{base}-Italic{ext}", f"{base}i{ext}"])
        if "bold" in style and "italic" in style:
            candidates.extend(["DejaVuSans-BoldOblique.ttf", "Arial Bold Italic.ttf", "arialbi.ttf"])
        elif "bold" in style:
            candidates.extend(["DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"])
        elif "italic" in style:
            candidates.extend(["DejaVuSans-Oblique.ttf", "Arial Italic.ttf", "ariali.ttf"])
        candidates.extend(["DejaVuSans.ttf", "Arial.ttf", "arial.ttf"])

        for font_name in candidates:
            try:
                return ImageFont.truetype(font_name, size=size)
            except (OSError, TypeError):
                continue
        return ImageFont.load_default()

    @staticmethod
    def _button_color(value, default="black"):
        try:
            ImageColor.getrgb(value or default)
            return value or default
        except (TypeError, ValueError):
            return default

    def _draw_curved_button_text(self, img, text_config, circle_bbox):
        text = str((text_config or {}).get("text") or "")
        if not text:
            return

        draw = ImageDraw.Draw(img)
        font_size = max(1, int(round(float(text_config.get("font_size") or 48))))
        font = self._load_button_font(
            text_config.get("font_family"),
            font_size,
            text_config.get("style", "Regular"),
        )
        fill = self._button_color(text_config.get("color"), "black")
        try:
            spacing = float(text_config.get("char_spacing") or 0)
        except (TypeError, ValueError):
            spacing = 0
        try:
            radius_offset = float(text_config.get("radius_offset") or 0)
        except (TypeError, ValueError):
            radius_offset = 0

        left, top, right, bottom = circle_bbox
        center = ((left + right) / 2, (top + bottom) / 2)
        radius = max(1, (right - left) / 2 - font_size / 2 - radius_offset)
        position = str(text_config.get("position") or "top").lower()
        anchor_degrees = {
            "top": -90,
            "12": -90,
            "12 o'clock": -90,
            "right": 0,
            "3": 0,
            "3 o'clock": 0,
            "bottom": 90,
            "6": 90,
            "6 o'clock": 90,
            "left": 180,
            "9": 180,
            "9 o'clock": 180,
        }.get(position, -90)
        inward = bool(text_config.get("inward"))

        widths = []
        heights = []
        for char in text:
            bbox = draw.textbbox((0, 0), char, font=font)
            widths.append(max(1, bbox[2] - bbox[0]))
            heights.append(max(1, bbox[3] - bbox[1]))
        advances = [width + spacing for width in widths]
        total_arc = max(0, sum(advances) - spacing)
        cursor = -total_arc / 2
        arc_direction = -1 if anchor_degrees in {90, 180} else 1

        for idx, char in enumerate(text):
            advance = advances[idx]
            midpoint = cursor + advance / 2
            angle = math.radians(anchor_degrees) + (arc_direction * midpoint / radius)
            x = center[0] + math.cos(angle) * radius
            y = center[1] + math.sin(angle) * radius
            rotation = math.degrees(angle) + (90 * arc_direction)
            if inward:
                rotation += 180

            char_w = widths[idx] + 8
            char_h = heights[idx] + 8
            char_img = Image.new("RGBA", (char_w, char_h), (255, 255, 255, 0))
            char_draw = ImageDraw.Draw(char_img)
            char_draw.text((char_w / 2, char_h / 2), char, fill=fill, font=font, anchor="mm")
            rotated = char_img.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
            img.paste(
                rotated.convert("RGB"),
                (round(x - rotated.width / 2), round(y - rotated.height / 2)),
                rotated,
            )
            cursor += advance

    def render_button_sheet(
        self,
        img,
        scale=None,
        offset=None,
        circle_diameter=None,
        finished_diameter=None,
        print_finished_circle=False,
        curved_text=None,
        print_lime_calibration_rectangle=False,
        lime_rectangle_width=None,
    ):
        """Render a circular button image centered on a 4x6 sheet."""
        if not HAS_PIL:
            raise RuntimeError("Please run: pip install pillow")

        source = img.convert("RGB") if img.mode != "RGB" else img
        crop_w, crop_h = BUTTON_CROP_SIZE
        sheet_w, sheet_h = BUTTON_PRINT_SIZE
        circle_diameter = self._clamp_button_diameter(circle_diameter)
        finished_diameter = self._clamp_button_diameter(
            finished_diameter,
            default=min(BUTTON_DEFAULT_FINISHED_DIAMETER, circle_diameter),
        )
        finished_diameter = min(finished_diameter, circle_diameter)
        if scale is None:
            scale = max(crop_w / source.width, crop_h / source.height)
        scale = max(float(scale), 0.01)
        resized_size = (
            max(1, round(source.width * scale)),
            max(1, round(source.height * scale)),
        )
        if offset is None:
            offset = (
                round((crop_w - resized_size[0]) / 2),
                round((crop_h - resized_size[1]) / 2),
            )
        else:
            offset = (round(offset[0]), round(offset[1]))

        resized = source.resize(resized_size, Image.Resampling.LANCZOS)
        crop = Image.new("RGB", BUTTON_CROP_SIZE, "white")
        crop.paste(resized, offset)

        circle_mask = Image.new("L", BUTTON_CROP_SIZE, 0)
        draw = ImageDraw.Draw(circle_mask)
        circle_left = (crop_w - circle_diameter) // 2
        circle_top = (crop_h - circle_diameter) // 2
        circle_bbox = (
            circle_left,
            circle_top,
            circle_left + circle_diameter - 1,
            circle_top + circle_diameter - 1,
        )
        draw.ellipse(circle_bbox, fill=255)
        circled = Image.new("RGB", BUTTON_CROP_SIZE, "white")
        circled.paste(crop, (0, 0), circle_mask)
        overlay = ImageDraw.Draw(circled)
        if print_finished_circle:
            finished_left = (crop_w - finished_diameter) // 2
            finished_top = (crop_h - finished_diameter) // 2
            overlay.ellipse(
                (
                    finished_left,
                    finished_top,
                    finished_left + finished_diameter - 1,
                    finished_top + finished_diameter - 1,
                ),
                outline="red",
                width=max(2, round(circle_diameter / 300)),
            )
        self._draw_curved_button_text(circled, curved_text, circle_bbox)

        sheet = Image.new("RGB", BUTTON_PRINT_SIZE, "white")
        sheet.paste(circled, ((sheet_w - crop_w) // 2, (sheet_h - crop_h) // 2))
        if print_lime_calibration_rectangle:
            self._draw_lime_calibration_rectangle(sheet, lime_rectangle_width)
        return sheet

    def _center_crop_to_print_ratio(self, img, size_key):
        """Center-crop *img* to the target print aspect ratio for *size_key*.

        Returns *img* unchanged when *size_key* is not in PRINT_ASPECT_RATIOS
        (e.g. button, magnet, statuettes) so existing flows are unaffected.
        """
        ratio = PRINT_ASPECT_RATIOS.get(size_key)
        if ratio is None or not HAS_PIL:
            return img
        short_r, long_r = ratio
        img_w, img_h = img.size
        if img_w <= img_h:
            # Portrait: width is the short side, height is the long side.
            target_h = img_h
            target_w = round(img_h * short_r / long_r)
            if target_w > img_w:
                target_w = img_w
                target_h = round(img_w * long_r / short_r)
        else:
            # Landscape: width is the long side, height is the short side.
            target_w = img_w
            target_h = round(img_w * short_r / long_r)
            if target_h > img_h:
                target_h = img_h
                target_w = round(img_h * long_r / short_r)
        return ImageOps.fit(
            img,
            (target_w, target_h),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    def _prepare_image_for_job(self, job: PrintJob):
        if job.source_type == "address":
            return self._render_address_label(job.address)
        if job.source_type == "pil":
            img = job.source
            return img.convert("RGB") if img.mode != "RGB" else img
        img = self._load_image_for_job(job)
        if job.size_key == "wallet":
            return self._build_wallet_sheet(img)
        return self._center_crop_to_print_ratio(img, job.size_key)

    def execute_print_job(self, job: PrintJob, fallback_printer=None):
        if not HAS_WIN32 or not HAS_PIL:
            raise RuntimeError("Please run: pip install pywin32 pillow")

        target_printer = job.routed_printer or self.get_routed_printer_for_key(job.size_key) or fallback_printer
        if not target_printer:
            raise RuntimeError("No printer resolved for this job")

        img = self._prepare_image_for_job(job)

        hdc = win32ui.CreateDC()
        try:
            hdc.CreatePrinterDC(target_printer)
            printable_w = hdc.GetDeviceCaps(win32con.HORZRES)
            printable_h = hdc.GetDeviceCaps(win32con.VERTRES)
            if printable_w <= 0 or printable_h <= 0:
                raise RuntimeError(f"Printer returned invalid printable area: {printable_w}x{printable_h}")

            if (img.width > img.height) != (printable_w > printable_h):
                img = img.rotate(90, expand=True)

            img = ImageOps.fit(
                img,
                (printable_w, printable_h),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )

            hdc.StartDoc(job.display_name)
            hdc.StartPage()
            dib = ImageWin.Dib(img)
            dib.draw(hdc.GetHandleOutput(), (0, 0, printable_w, printable_h))
            hdc.EndPage()
            hdc.EndDoc()
        finally:
            try:
                hdc.DeleteDC()
            except Exception:
                pass
