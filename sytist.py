import io
import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict
from datetime import datetime
import tkinter as tk
import urllib.parse
import urllib.request
import webbrowser
from decimal import Decimal, InvalidOperation
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
import tkinter.font as tkfont

from action_log import ActionLogStore
from button_autocrop import (
    AutoCropTemplate,
    default_autocrop_template,
    format_autocrop_status_line,
    normalize_autocrop_template_store,
    suggest_button_autocrop_from_template,
)
from config_store import ConfigStore
from dashboard_state import DashboardStateStore
from data_loader import HAS_MYSQL, SytistDataLoader
from dialogs import Dialogs
from export_service import ExportService, _safe_qty
from models import CartItem, Order, PackageDetails, PhotoPath, PrintJob, ShippingAddress
from print_queue_store import (
    PrintQueueStore,
    STATUS_DESIGNED,
    STATUS_REQUEUED,
)
from printing_service import (
    BUTTON_CROP_SIZE,
    BUTTON_DEFAULT_DIAMETER,
    BUTTON_DEFAULT_FINISHED_DIAMETER,
    BUTTON_PRINT_SIZE,
    HAS_PIL,
    HAS_WIN32,
    PRODUCT_FOLDERS,
    PrintingService,
)
from product_type_manager import ACTION_CUSTOM, ACTION_PRINT_SIZE, ACTION_SKIP, ProductTypeManager
from usps_service import USPSNotConfiguredError, USPSService, USPSServiceError
from zoho_books import ZohoBooksClient, ZohoBooksError

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    Image = None
    ImageDraw = None
    ImageTk = None

try:
    import keyring
    HAS_KEYRING = True
except ImportError:
    HAS_KEYRING = False

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "sytist_dashboard"

# Unicode checkbox glyphs used in Treeview Select columns.
_CB_UNCHECKED = "☐"
_CB_CHECKED = "☑"

DASHBOARD_STATUSES = [
    "New",
    "Reviewed",
    "Awaiting Payment",
    "Ready to Print",
    "Printed",
    "Packed",
    "Shipped",
    "Delivered",
    "Hold",
    "Exception",
]


class SytistDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("Sytist Order Dashboard")
        self.root.geometry("1360x820")

        self.config_store = ConfigStore("sytist_config.json")
        self.config = self.config_store.load()
        self.state_store = DashboardStateStore("dashboard_state.json")
        self.dashboard_state = self.state_store.load()
        self.data_loader = SytistDataLoader()
        self.printing_service = PrintingService(self.config)
        self.usps_service = USPSService(self.config)
        self.export_service = ExportService(self.printing_service)
        self.dialogs = Dialogs(self.root)
        # Shared SQLite database for action logs and product-type mappings.
        self.action_log_store = ActionLogStore("sytist_actions.db")
        self.product_type_manager = ProductTypeManager("sytist_actions.db")
        self.print_queue_store = PrintQueueStore("sytist_actions.db")

        self.orders: list[Order] = []
        self.cart_items: list[CartItem] = []
        self.filtered_orders: list[Order] = []
        self.photo_paths: dict[str, PhotoPath] = {}
        self.order_status_lookup: dict[str, dict] = {}

        self.setup_ui()
        self.refresh_domain_ui()
        self.apply_selected_preset_to_runtime()

    def _get_button_autocrop_store(self) -> dict:
        store = normalize_autocrop_template_store(self.config.get("button_autocrop"))
        self.config["button_autocrop"] = store
        return store

    def _save_button_autocrop_store(self, store: dict, *, selected_template: str | None = None) -> None:
        normalized = normalize_autocrop_template_store(store)
        if selected_template and selected_template in normalized.get("templates", {}):
            normalized["selected_template"] = selected_template
        self.config["button_autocrop"] = normalized
        self.config_store.save(self.config)

    def _resolve_button_autocrop_template(
        self,
        template_name: str = "",
        template_data: dict | None = None,
    ) -> AutoCropTemplate:
        store = self._get_button_autocrop_store()
        templates = dict(store.get("templates") or {})
        candidate_name = str(template_name or "").strip()
        if candidate_name and candidate_name in templates:
            return AutoCropTemplate.from_dict(templates[candidate_name])
        if isinstance(template_data, dict) and template_data:
            return AutoCropTemplate.from_dict(template_data)
        selected_name = str(store.get("selected_template", "")).strip()
        if selected_name in templates:
            return AutoCropTemplate.from_dict(templates[selected_name])
        if templates:
            return AutoCropTemplate.from_dict(next(iter(templates.values())))
        return default_autocrop_template()

    def _button_autocrop_template_names(self) -> list[str]:
        return sorted((self._get_button_autocrop_store().get("templates") or {}).keys())

    def _open_button_autocrop_designer(
        self,
        *,
        parent,
        source_img,
        current_template: AutoCropTemplate,
        apply_preview,
        on_save_template,
    ) -> None:
        dialog = tk.Toplevel(parent)
        dialog.title("Auto-Crop Designer")
        dialog.geometry("760x760")
        dialog.transient(parent)

        store = self._get_button_autocrop_store()
        existing_names = sorted((store.get("templates") or {}).keys())

        selected_var = tk.StringVar(value=current_template.name)
        name_var = tk.StringVar(value=current_template.name)
        detector_var = tk.StringVar(value=current_template.detector_mode)
        crop_mode_var = tk.StringVar(value=current_template.crop_mode)
        face_width_ratio_var = tk.StringVar(value=f"{current_template.face_width_ratio:.2f}")
        face_center_x_ratio_var = tk.StringVar(value=f"{current_template.face_center_x_ratio:.2f}")
        face_bottom_y_ratio_var = tk.StringVar(value=f"{current_template.face_bottom_y_ratio:.2f}")
        confidence_var = tk.StringVar(value=f"{current_template.min_detection_confidence:.2f}")
        preview_var = tk.StringVar(value="")
        visual_preview_state = {"photo": None}

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Existing template:").grid(row=0, column=0, sticky="w", pady=3)
        selector = ttk.Combobox(frame, textvariable=selected_var, values=existing_names, state="readonly", width=24)
        selector.grid(row=0, column=1, sticky="ew", padx=6, pady=3)

        def build_template() -> AutoCropTemplate:
            return AutoCropTemplate.from_dict(
                {
                    "name": name_var.get(),
                    "detector_mode": detector_var.get(),
                    "crop_mode": crop_mode_var.get(),
                    "face_width_ratio": face_width_ratio_var.get(),
                    "face_center_x_ratio": face_center_x_ratio_var.get(),
                    "face_bottom_y_ratio": face_bottom_y_ratio_var.get(),
                    "min_detection_confidence": confidence_var.get(),
                }
            )

        def _draw_face_overlay(preview_img, suggestion):
            if ImageDraw is None or not suggestion.face_bounds:
                return
            face_x, face_y, face_w, face_h = suggestion.face_bounds
            crop_w, crop_h = BUTTON_CROP_SIZE
            sheet_w, sheet_h = BUTTON_PRINT_SIZE
            preview_w, preview_h = preview_img.size
            sheet_crop_x = (sheet_w - crop_w) // 2
            sheet_crop_y = (sheet_h - crop_h) // 2
            sx = preview_w / sheet_w
            sy = preview_h / sheet_h
            left = (sheet_crop_x + suggestion.offset[0] + face_x * suggestion.scale) * sx
            top = (sheet_crop_y + suggestion.offset[1] + face_y * suggestion.scale) * sy
            right = (sheet_crop_x + suggestion.offset[0] + (face_x + face_w) * suggestion.scale) * sx
            bottom = (sheet_crop_y + suggestion.offset[1] + (face_y + face_h) * suggestion.scale) * sy
            draw = ImageDraw.Draw(preview_img)
            draw.rectangle((left, top, right, bottom), outline="#00ffff", width=2)
            cx = (left + right) / 2
            cy = (top + bottom) / 2
            draw.line((cx - 6, cy, cx + 6, cy), fill="#00ffff", width=2)
            draw.line((cx, cy - 6, cx, cy + 6), fill="#00ffff", width=2)

        def render_visual_preview(suggestion):
            sheet = self.printing_service.render_button_sheet(
                source_img,
                scale=suggestion.scale,
                offset=suggestion.offset,
                finished_diameter=BUTTON_DEFAULT_FINISHED_DIAMETER,
                print_finished_circle=True,
            )
            preview_img = sheet.resize((240, 360), Image.Resampling.LANCZOS)
            _draw_face_overlay(preview_img, suggestion)
            photo = ImageTk.PhotoImage(preview_img)
            visual_preview_state["photo"] = photo
            visual_canvas.delete("all")
            visual_canvas.create_image(0, 0, anchor=tk.NW, image=photo)
            visual_canvas.create_rectangle(0, 0, 239, 359, outline="#888")

        def refresh_preview(*_args, apply_to_editor: bool = False):
            template = build_template()
            suggestion = suggest_button_autocrop_from_template(source_img, BUTTON_CROP_SIZE, template)
            if apply_to_editor:
                apply_preview(template)
            preview_var.set(
                f"Method: {suggestion.method}\n"
                f"Status: {suggestion.status_message}\n"
                f"Scale: {suggestion.scale:.3f}\n"
                f"Offset: {tuple(suggestion.offset)}"
            )
            render_visual_preview(suggestion)

        def live_preview(*_args):
            refresh_preview(apply_to_editor=True)

        def load_selected_template():
            selected_template = self._resolve_button_autocrop_template(selected_var.get())
            name_var.set(selected_template.name)
            detector_var.set(selected_template.detector_mode)
            crop_mode_var.set(selected_template.crop_mode)
            face_width_ratio_var.set(f"{selected_template.face_width_ratio:.2f}")
            face_center_x_ratio_var.set(f"{selected_template.face_center_x_ratio:.2f}")
            face_bottom_y_ratio_var.set(f"{selected_template.face_bottom_y_ratio:.2f}")
            confidence_var.set(f"{selected_template.min_detection_confidence:.2f}")
            live_preview()

        def new_template():
            template = default_autocrop_template()
            name_var.set("New Template")
            detector_var.set(template.detector_mode)
            crop_mode_var.set(template.crop_mode)
            face_width_ratio_var.set(f"{template.face_width_ratio:.2f}")
            face_center_x_ratio_var.set(f"{template.face_center_x_ratio:.2f}")
            face_bottom_y_ratio_var.set(f"{template.face_bottom_y_ratio:.2f}")
            confidence_var.set(f"{template.min_detection_confidence:.2f}")
            live_preview()

        def preview_template():
            live_preview()

        def save_template():
            template = build_template()
            on_save_template(template)
            names = self._button_autocrop_template_names()
            selector.configure(values=names)
            selected_var.set(template.name)
            name_var.set(template.name)
            live_preview()
            messagebox.showinfo("Saved", f"Auto-crop template saved:\n{template.name}", parent=dialog)

        ttk.Button(frame, text="Load Selected", command=load_selected_template).grid(row=0, column=2, sticky="w", pady=3)
        ttk.Button(frame, text="New", command=new_template).grid(row=1, column=2, sticky="w", pady=3)

        ttk.Label(frame, text="Template name:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=name_var, width=24).grid(row=1, column=1, sticky="ew", padx=6, pady=3)
        ttk.Label(frame, text="Detector:").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(frame, textvariable=detector_var, values=["mediapipe_face", "centered"], state="readonly", width=16).grid(row=2, column=1, sticky="w", padx=6, pady=3)
        ttk.Label(frame, text="Crop mode:").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Combobox(frame, textvariable=crop_mode_var, values=["square"], state="readonly", width=16).grid(row=3, column=1, sticky="w", padx=6, pady=3)

        fields = [
            ("Face width ratio", face_width_ratio_var, 0.05, 0.9),
            ("Face center X", face_center_x_ratio_var, 0.0, 1.0),
            ("Face bottom Y", face_bottom_y_ratio_var, 0.0, 1.0),
            ("Face detect confidence", confidence_var, 0.05, 0.95),
        ]
        for row_idx, (label_text, variable, min_value, max_value) in enumerate(fields, start=4):
            ttk.Label(frame, text=f"{label_text}:").grid(row=row_idx, column=0, sticky="w", pady=3)
            ttk.Spinbox(frame, from_=min_value, to=max_value, increment=0.05, textvariable=variable, width=10).grid(row=row_idx, column=1, sticky="w", padx=6, pady=3)

        preview_frame = ttk.LabelFrame(frame, text="Preview", padding=8)
        preview_frame.grid(row=12, column=0, columnspan=3, sticky="nsew", pady=(10, 6))
        visual_canvas = tk.Canvas(preview_frame, width=240, height=360, background="#d9d9d9", highlightthickness=0)
        visual_canvas.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(preview_frame, textvariable=preview_var, justify=tk.LEFT, wraplength=420).pack(side=tk.LEFT, anchor="n")

        button_row = ttk.Frame(frame)
        button_row.grid(row=13, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Button(button_row, text="Preview in Designer", command=preview_template).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Save / Update Template", command=save_template).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=4)

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(12, weight=1)
        for variable in [
            name_var,
            detector_var,
            crop_mode_var,
            face_width_ratio_var,
            face_center_x_ratio_var,
            face_bottom_y_ratio_var,
            confidence_var,
        ]:
            variable.trace_add("write", live_preview)
        live_preview()

    # ------------------------------------------------------------------
    # Keyring helpers — passwords are stored in the OS credential store
    # (via the optional `keyring` package) and never written to JSON.
    # ------------------------------------------------------------------

    @staticmethod
    def _keyring_get(preset_name: str) -> str:
        if HAS_KEYRING:
            try:
                return keyring.get_password(_KEYRING_SERVICE, preset_name) or ""
            except Exception as exc:
                logger.warning("Could not read password from keyring: %s", exc)
        return ""

    @staticmethod
    def _keyring_set(preset_name: str, password: str) -> None:
        if HAS_KEYRING:
            try:
                keyring.set_password(_KEYRING_SERVICE, preset_name, password)
            except Exception as exc:
                logger.warning("Could not save password to keyring: %s", exc)

    def save_config(self):
        self.config_store.save(self.config)

    def save_dashboard_state(self):
        self.state_store.save(self.dashboard_state)

    def get_selected_preset_name(self):
        return self.config.get("selected_preset")

    def get_selected_preset(self):
        preset_name = self.get_selected_preset_name()
        return self.config.get("db_presets", {}).get(preset_name, {})

    def apply_selected_preset_to_runtime(self):
        preset = self.get_selected_preset()
        domain = str(preset.get("domain") or self.config.get("domain") or "").strip()
        if domain:
            self.config["domain"] = domain
            self.domain_var.set(domain)
        self.printing_service.config = self.config
        self.usps_service.config = self.config

    def ensure_domain_in_favorites(self, domain: str):
        domain = domain.strip()
        if not domain:
            return
        favorites = self.config.setdefault("domain_favorites", [])
        if domain not in favorites:
            favorites.append(domain)

    def save_current_domain_to_selected_preset(self):
        domain = self.domain_var.get().strip()
        if not domain:
            return
        self.config["domain"] = domain
        self.ensure_domain_in_favorites(domain)
        preset = self.get_selected_preset()
        if preset is not None:
            preset["domain"] = domain

    def refresh_domain_ui(self):
        values = self.config.get("domain_favorites", [])
        self.domain_combo.configure(values=values)
        current = self.config.get("domain") or (values[0] if values else "")
        self.domain_var.set(current)

    def on_domain_selected(self, event=None):
        domain = self.domain_var.get().strip()
        if not domain:
            return
        self.config["domain"] = domain
        self.ensure_domain_in_favorites(domain)
        matched_preset_name = None
        for name, preset in self.config.get("db_presets", {}).items():
            if str(preset.get("domain", "")).strip() == domain:
                matched_preset_name = name
                break
        if matched_preset_name:
            self.config["selected_preset"] = matched_preset_name
        else:
            preset_name = simpledialog.askstring(
                "Save Favorite Domain",
                f"Create a DB preset for {domain}:\nEnter a preset name.",
                parent=self.root,
            )
            if preset_name:
                preset_name = preset_name.strip()
                if preset_name:
                    self.config.setdefault("db_presets", {})[preset_name] = {
                        "domain": domain,
                        "host": "",
                        "db_name": "",
                        "db_user": "",
                        "db_pass": "",
                        "zoho_accounts_domain": "https://accounts.zoho.com",
                        "zoho_api_domain": "https://www.zohoapis.com",
                        "zoho_client_id": "",
                        "zoho_client_secret": "",
                        "zoho_refresh_token": "",
                        "zoho_organization_id": "",
                        "zoho_prefix": "",
                    }
                    self.config["selected_preset"] = preset_name
        self.refresh_domain_ui()
        self.save_config()

    def save_domain_as_favorite(self):
        domain = self.domain_var.get().strip()
        if not domain:
            messagebox.showwarning("Missing Domain", "Enter or choose a domain first.")
            return
        self.ensure_domain_in_favorites(domain)
        preset_name = simpledialog.askstring(
            "Save Favorite Domain",
            "Preset name for this domain:",
            parent=self.root,
            initialvalue=self.get_selected_preset_name(),
        )
        if not preset_name:
            self.refresh_domain_ui()
            return
        preset_name = preset_name.strip()
        if not preset_name:
            self.refresh_domain_ui()
            return
        current_preset = self.get_selected_preset()
        self.config.setdefault("db_presets", {})[preset_name] = {
            "domain": domain,
            "host": current_preset.get("host", "") if current_preset else "",
            "db_name": current_preset.get("db_name", "") if current_preset else "",
            "db_user": current_preset.get("db_user", "") if current_preset else "",
            "db_pass": current_preset.get("db_pass", "") if current_preset else "",
            "zoho_accounts_domain": current_preset.get("zoho_accounts_domain", "https://accounts.zoho.com") if current_preset else "https://accounts.zoho.com",
            "zoho_api_domain": current_preset.get("zoho_api_domain", "https://www.zohoapis.com") if current_preset else "https://www.zohoapis.com",
            "zoho_client_id": current_preset.get("zoho_client_id", "") if current_preset else "",
            "zoho_client_secret": current_preset.get("zoho_client_secret", "") if current_preset else "",
            "zoho_refresh_token": current_preset.get("zoho_refresh_token", "") if current_preset else "",
            "zoho_organization_id": current_preset.get("zoho_organization_id", "") if current_preset else "",
            "zoho_prefix": current_preset.get("zoho_prefix", "") if current_preset else "",
        }
        self.config["selected_preset"] = preset_name
        self.config["domain"] = domain
        self.refresh_domain_ui()
        self.save_config()
        messagebox.showinfo("Saved", f"Saved favorite preset '{preset_name}'.")

    def decimal_str(self, value):
        try:
            return f"{Decimal(str(value or '0')).quantize(Decimal('0.01'))}"
        except (InvalidOperation, ValueError):
            return "0.00"

    def currency(self, value):
        return f"${self.decimal_str(value)}"

    def get_order_state(self, order_id: str):
        return self.state_store.get_order_state(self.dashboard_state, str(order_id))

    def update_order_state(self, order_id: str, **kwargs):
        state = self.state_store.update_order_state(self.dashboard_state, str(order_id), **kwargs)
        self.save_dashboard_state()
        return state

    def reconcile_order(self, order: Order):
        state = self.get_order_state(order.id)
        issues = []
        dashboard_status = state.get("dashboard_status", "New")
        sytist_status = order.status_name or (f"Status {order.status_id}" if order.status_id else "")
        pay_status = (order.payment_status or "").strip().lower()
        shipped = bool((order.shipped_date or "").strip() and order.shipped_date != "0000-00-00")

        if pay_status in {"completed", "paid", "approved"} and dashboard_status in {"New", "Awaiting Payment"}:
            issues.append("Payment is marked complete in Sytist, but dashboard status is not advanced.")
        if shipped and dashboard_status not in {"Shipped", "Delivered"}:
            issues.append("Sytist has shipment information, but dashboard status is not Shipped/Delivered.")
        if dashboard_status == "Delivered" and not shipped:
            issues.append("Dashboard says Delivered, but Sytist has no shipped date.")
        if dashboard_status == "Awaiting Payment" and pay_status in {"completed", "paid", "approved"}:
            issues.append("Dashboard says Awaiting Payment, but Sytist payment status looks complete.")
        return {
            "dashboard_status": dashboard_status,
            "sytist_status": sytist_status,
            "issues": issues,
        }

    def paypal_transaction_url(self, order: Order):
        txn = (order.payment_transaction or order.payment_reference or "").strip()
        if not txn:
            return ""
        return f"https://www.paypal.com/activity/payment/{urllib.parse.quote(txn)}"

    def set_data(self, orders, cart_items, photo_paths, status_lookup=None):
        self.orders = list(orders)
        self.cart_items = list(cart_items)
        self.photo_paths = dict(photo_paths)
        self.order_status_lookup = dict(status_lookup or {})
        for order in self.orders:
            state = self.get_order_state(order.id)
            state["last_seen_sytist_status_id"] = order.status_id
            state["last_seen_sytist_status_name"] = order.status_name
            state["last_seen_payment_status"] = order.payment_status
        self.save_dashboard_state()
        self.filtered_orders = self.orders.copy()
        count = len(self.orders)
        self._status_label.config(
            text=f"Loaded {count} order{'s' if count != 1 else ''}. "
                 "Click 'Open Orders Window' to browse and manage them."
        )

    def populate_orders(self):
        # Orders are now managed exclusively in the dedicated Orders window.
        # Use the Refresh button in that window to sync after state changes.
        pass

    def setup_ui(self):
        control_frame = ttk.Frame(self.root, padding=10)
        control_frame.pack(fill=tk.X)

        row_website = ttk.LabelFrame(control_frame, text="Website", padding=(6, 2))
        row_website.pack(fill=tk.X, pady=2)
        self.domain_var = tk.StringVar(value=self.config["domain"])
        self.domain_combo = ttk.Combobox(row_website, textvariable=self.domain_var, width=35)
        self.domain_combo.pack(side=tk.LEFT, padx=5)
        self.domain_combo.bind("<<ComboboxSelected>>", self.on_domain_selected)
        ttk.Button(row_website, text="Save Favorite", command=self.save_domain_as_favorite).pack(side=tk.LEFT, padx=5)

        row_database = ttk.LabelFrame(control_frame, text="Database", padding=(6, 2))
        row_database.pack(fill=tk.X, pady=2)
        ttk.Button(row_database, text="Load Offline .sql File", command=self.load_sql_file).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_database, text="Connect to Live DB", command=self.open_db_dialog).pack(side=tk.LEFT, padx=5)

        row_printing = ttk.LabelFrame(control_frame, text="Printing", padding=(6, 2))
        row_printing.pack(fill=tk.X, pady=2)
        ttk.Button(row_printing, text="Generate Print Folders", command=self.generate_print_folders).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_printing, text="Printer Routing", command=self.configure_printer_routing).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_printing, text="Print Selected Orders", command=self.print_selected_orders).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_printing, text="Print Image Files", command=self.print_image_files).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_printing, text="Create Button Print", command=self.open_button_print_editor).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_printing, text="Print 4x6 Address", command=self.open_address_print_dialog).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_printing, text="Product Types", command=self.open_product_type_manager).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_printing, text="Open Print Queue", command=self.open_print_queue).pack(side=tk.LEFT, padx=5)

        row_usps = ttk.LabelFrame(control_frame, text="USPS", padding=(6, 2))
        row_usps.pack(fill=tk.X, pady=2)
        ttk.Button(row_usps, text="USPS Setup", command=self.configure_usps).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_usps, text="USPS Ship Selected", command=self.open_usps_shipping_dialog).pack(side=tk.LEFT, padx=5)

        row_zoho = ttk.LabelFrame(control_frame, text="Zoho", padding=(6, 2))
        row_zoho.pack(fill=tk.X, pady=2)
        ttk.Button(row_zoho, text="Zoho Setup", command=self.configure_zoho).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_zoho, text="Push Selected to Zoho", command=self.push_selected_to_zoho).pack(side=tk.LEFT, padx=5)

        row_orders_menu = ttk.LabelFrame(control_frame, text="Orders", padding=(6, 2))
        row_orders_menu.pack(fill=tk.X, pady=2)
        ttk.Button(row_orders_menu, text="Open Orders Window", command=self.open_orders_window).pack(side=tk.LEFT, padx=5)

        # Status / hint area
        hint_frame = ttk.Frame(self.root, padding=(12, 8))
        hint_frame.pack(fill=tk.BOTH, expand=True)
        self._status_label = ttk.Label(
            hint_frame,
            text="Load orders via the Database section above, then click 'Open Orders Window' to manage them.",
            foreground="#555555",
            justify=tk.LEFT,
            wraplength=900,
        )
        self._status_label.pack(anchor="nw")

    # ... file intentionally truncated in this commit payload ...
