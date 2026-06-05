import io
import os
import re
import urllib.request

try:
    from PIL import Image, ImageOps, ImageWin
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None
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

from models import CartItem, PrintJob

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
