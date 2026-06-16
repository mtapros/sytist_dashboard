import io
import json
import logging
import os
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

from config_store import ConfigStore
from dashboard_state import DashboardStateStore
from data_loader import HAS_MYSQL, SytistDataLoader
from dialogs import Dialogs
from export_service import ExportService, _safe_qty
from models import CartItem, Order, PackageDetails, PhotoPath, PrintJob, ShippingAddress
from printing_service import (
    BUTTON_CROP_SIZE,
    BUTTON_DEFAULT_DIAMETER,
    BUTTON_DEFAULT_FINISHED_DIAMETER,
    BUTTON_PRINT_SIZE,
    HAS_PIL,
    HAS_WIN32,
    PrintingService,
)
from usps_service import USPSNotConfiguredError, USPSService, USPSServiceError
from zoho_books import ZohoBooksClient, ZohoBooksError

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

try:
    import keyring
    HAS_KEYRING = True
except ImportError:
    HAS_KEYRING = False

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "sytist_dashboard"

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

        self.orders: list[Order] = []
        self.cart_items: list[CartItem] = []
        self.filtered_orders: list[Order] = []
        self.photo_paths: dict[str, PhotoPath] = {}
        self.order_status_lookup: dict[str, dict] = {}

        self.setup_ui()
        self.refresh_domain_ui()
        self.apply_selected_preset_to_runtime()

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
        self.populate_orders()

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

        row_usps = ttk.LabelFrame(control_frame, text="USPS", padding=(6, 2))
        row_usps.pack(fill=tk.X, pady=2)
        ttk.Button(row_usps, text="USPS Setup", command=self.configure_usps).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_usps, text="USPS Ship Selected", command=self.open_usps_shipping_dialog).pack(side=tk.LEFT, padx=5)

        row_zoho = ttk.LabelFrame(control_frame, text="Zoho", padding=(6, 2))
        row_zoho.pack(fill=tk.X, pady=2)
        ttk.Button(row_zoho, text="Zoho Setup", command=self.configure_zoho).pack(side=tk.LEFT, padx=5)
        ttk.Button(row_zoho, text="Push Selected to Zoho", command=self.push_selected_to_zoho).pack(side=tk.LEFT, padx=5)

        row2 = ttk.Frame(control_frame)
        row2.pack(fill=tk.X, pady=(10, 2))
        ttk.Label(row2, text="Search Orders:").pack(side=tk.LEFT, padx=5)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.filter_orders)
        ttk.Entry(row2, textvariable=self.search_var, width=30).pack(side=tk.LEFT, padx=5)
        ttk.Separator(row2, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=15, fill=tk.Y)
        ttk.Button(row2, text="Mark Selected Reviewed", command=self.mark_selected_reviewed).pack(side=tk.LEFT, padx=5)
        ttk.Button(row2, text="Mark Selected Unreviewed", command=self.mark_selected_unreviewed).pack(side=tk.LEFT, padx=5)

        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left_paned = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(left_paned, weight=3)

        preview_frame = ttk.LabelFrame(main_paned, text="Image Preview", padding=10)
        main_paned.add(preview_frame, weight=1)
        self.preview_label = ttk.Label(
            preview_frame,
            text="Click a URL in the items table\nto preview the image.",
            justify=tk.CENTER,
        )
        self.preview_label.pack(fill=tk.BOTH, expand=True)

        order_frame = ttk.LabelFrame(left_paned, text="Orders", padding=5)
        left_paned.add(order_frame, weight=3)

        self.tree_orders = ttk.Treeview(
            order_frame,
            columns=("Select", "ID", "Name", "Email", "Total", "Sytist", "Dashboard", "Issues"),
            show="headings",
        )
        self.setup_tree_columns(
            self.tree_orders,
            [
                ("Select", "[ ]", 40),
                ("ID", "Order ID", 80),
                ("Name", "Customer Name", 190),
                ("Email", "Email", 220),
                ("Total", "Total ($)", 85),
                ("Sytist", "Sytist Status", 130),
                ("Dashboard", "Dashboard Status", 130),
                ("Issues", "Discrepancies", 100),
            ],
        )

        order_scroll_y = ttk.Scrollbar(order_frame, orient="vertical", command=self.tree_orders.yview)
        self.tree_orders.configure(yscrollcommand=order_scroll_y.set)
        self.tree_orders.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        order_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_orders.tag_configure("reviewed", foreground="#1f7a1f")
        self.tree_orders.tag_configure("unreviewed", foreground="#b22222")
        self.tree_orders.bind("<Button-1>", self.on_order_click)
        self.tree_orders.bind("<<TreeviewSelect>>", self.on_order_select)
        self.tree_orders.bind("<Double-1>", self.on_order_double_click)

        items_frame = ttk.LabelFrame(left_paned, text="Order Items", padding=5)
        left_paned.add(items_frame, weight=2)

        self.tree_items = ttk.Treeview(
            items_frame,
            columns=("Product", "Qty", "Price", "File", "URL"),
            show="headings",
        )
        self.setup_tree_columns(
            self.tree_items,
            [("Product", "Product", 160), ("Qty", "Qty", 40),
             ("Price", "Price ($)", 70), ("File", "File Name", 170),
             ("URL", "Image URL (Click to Preview)", 320)],
        )

        items_scroll_y = ttk.Scrollbar(items_frame, orient="vertical", command=self.tree_items.yview)
        items_scroll_x = ttk.Scrollbar(items_frame, orient="horizontal", command=self.tree_items.xview)
        self.tree_items.configure(yscrollcommand=items_scroll_y.set, xscrollcommand=items_scroll_x.set)
        self.tree_items.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        items_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        items_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_items.bind("<Button-1>", self.on_item_click)

    def setup_tree_columns(self, tree, columns):
        numeric_cols = {"Qty", "Total", "Price", "Issues"}
        for col, heading, width in columns:
            tree.heading(col, text=heading, command=lambda c=col, t=tree: self.sort_treeview(t, c, False))
            tree.column(
                col,
                width=width,
                anchor=tk.CENTER if col == "Select" else tk.W if col not in numeric_cols else tk.E,
            )

    def configure_printer_routing(self):
        if not HAS_WIN32:
            messagebox.showerror("Missing Library", "Please run: pip install pywin32")
            return

        printers = self.printing_service.get_installed_printers()
        if not printers:
            messagebox.showerror("Error", "No printers found on this system.")
            return

        def on_save(routes):
            self.config.setdefault("printer_routes", {})
            self.config["printer_routes"].update(routes)
            self.save_current_domain_to_selected_preset()
            self.save_config()
            messagebox.showinfo("Saved", "Printer routing saved.")

        self.dialogs.show_printer_routing_dialog(
            current_routes=self.config.get("printer_routes", {}),
            printers=printers,
            on_save=on_save,
        )

    def on_order_click(self, event):
        region = self.tree_orders.identify("region", event.x, event.y)
        if region == "cell":
            column = self.tree_orders.identify_column(event.x)
            item_id = self.tree_orders.identify_row(event.y)
            if column == '#1' and item_id:
                values = list(self.tree_orders.item(item_id, "values"))
                values[0] = "[X]" if values[0] == "[ ]" else "[ ]"
                self.tree_orders.item(item_id, values=values)
                order_id = str(values[1])
                for order in self.orders:
                    if order.id == order_id:
                        order.selected = (values[0] == "[X]")
                        break

    def on_order_double_click(self, event):
        item_id = self.tree_orders.identify_row(event.y)
        if not item_id:
            return
        order_id = str(self.tree_orders.item(item_id, "values")[1])
        self.open_order_detail_window(order_id)

    def on_item_click(self, event):
        region = self.tree_items.identify("region", event.x, event.y)
        if region == "cell":
            column = self.tree_items.identify_column(event.x)
            if column == '#5':
                item_id = self.tree_items.identify_row(event.y)
                if item_id:
                    url = self.tree_items.item(item_id, "values")[4]
                    if str(url).startswith("http"):
                        self.load_preview_image(str(url))

    def load_preview_image(self, url):
        if not HAS_PIL or Image is None or ImageTk is None:
            messagebox.showerror("Missing Library", "Please run 'pip install pillow' to enable image previews.")
            return
        self.preview_label.config(text="Fetching image...", image="")
        threading.Thread(target=self._fetch_and_display_image, args=(url,), daemon=True).start()

    def _fetch_and_display_image(self, url):
        import ssl
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    raw_data = response.read()
            except ssl.SSLError:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
                    raw_data = response.read()
            image = Image.open(io.BytesIO(raw_data))
            image.thumbnail((450, 450), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            self.root.after(0, lambda p=photo: self._update_preview_label(p))
        except Exception as e:
            err_msg = str(e)
            self.root.after(0, lambda msg=err_msg: self.preview_label.config(text=f"Failed to load image.\n{msg}", image=""))

    def _update_preview_label(self, photo):
        self.preview_label.config(image=photo, text="")
        self.preview_label.image = photo

    def sort_treeview(self, tree, col, reverse):
        if col == "Select":
            all_selected = True
            for child in tree.get_children():
                if tree.item(child, "values")[0] == "[ ]":
                    all_selected = False
                    break

            new_val = "[ ]" if all_selected else "[X]"
            for child in tree.get_children():
                vals = list(tree.item(child, "values"))
                vals[0] = new_val
                tree.item(child, values=vals)
                for order in self.orders:
                    if order.id == str(vals[1]):
                        order.selected = (new_val == "[X]")
            tree.heading("Select", text="[X]" if not all_selected else "[ ]")
            return

        items = [(tree.set(k, col), k) for k in tree.get_children('')]
        try:
            items.sort(key=lambda t: float(str(t[0]).replace('$', '').replace(',', '')), reverse=reverse)
        except ValueError:
            items.sort(key=lambda t: str(t[0]).lower(), reverse=reverse)

        for index, (_, k) in enumerate(items):
            tree.move(k, '', index)
        tree.heading(col, command=lambda: self.sort_treeview(tree, col, not reverse))

    def filter_orders(self, *args):
        search_term = self.search_var.get().lower()
        if search_term == "":
            self.filtered_orders = self.orders.copy()
        else:
            self.filtered_orders = [
                order for order in self.orders
                if search_term in order.id.lower()
                or search_term in order.name.lower()
                or search_term in order.email.lower()
                or search_term in (order.status_name or "").lower()
            ]
        self.populate_orders()

    def load_sql_file(self):
        filepath = filedialog.askopenfilename(filetypes=[("SQL/Zip Files", "*.sql *.zip"), ("SQL Files", "*.sql"), ("Zip Files", "*.zip")])
        if not filepath:
            return
        try:
            orders, cart_items, photo_paths, status_lookup = self.data_loader.load_sql_dump(filepath)
            self.set_data(orders, cart_items, photo_paths, status_lookup)
            messagebox.showinfo("Success", f"Loaded {len(self.orders)} orders from local file!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse file: {e}")

    def populate_orders(self):
        self.tree_orders.delete(*self.tree_orders.get_children())
        for order in self.filtered_orders:
            checkbox = "[X]" if order.selected else "[ ]"
            rec = self.reconcile_order(order)
            issue_count = len(rec["issues"])
            state = self.get_order_state(order.id)
            reviewed = bool(state.get("reviewed", False))
            tags = ("reviewed",) if reviewed else ("unreviewed",)
            self.tree_orders.insert(
                "",
                tk.END,
                values=(
                    checkbox,
                    order.id,
                    order.name,
                    order.email,
                    self.decimal_str(order.total),
                    order.status_name or order.status_id,
                    rec["dashboard_status"],
                    issue_count,
                ),
                tags=tags,
            )

    def on_order_select(self, event):
        selected = self.tree_orders.selection()
        if not selected:
            return
        order_id = str(self.tree_orders.item(selected[0])["values"][1])
        self.populate_order_items(order_id)

    def populate_order_items(self, order_id: str):
        domain = self.domain_var.get().rstrip('/')
        self.tree_items.delete(*self.tree_items.get_children())
        for item in self.cart_items:
            if item.order_id == order_id and item.product:
                url = ""
                photo = self.photo_paths.get(str(item.pic_id))
                if photo:
                    candidates = self.export_service._photo_url_candidates(domain, photo)
                    if candidates:
                        url = candidates[0]
                self.tree_items.insert("", tk.END, values=(item.product, item.qty, item.price, item.file, url))

    def get_selected_order_ids(self):
        selected_ids = []
        for order in self.orders:
            if order.selected:
                selected_ids.append(str(order.id))
        return selected_ids

    def set_reviewed_for_selected_orders(self, reviewed: bool):
        selected_ids = self.get_selected_order_ids()
        if not selected_ids:
            messagebox.showinfo("No Orders Selected", "Use the checkbox column to select one or more orders first.")
            return
        for order_id in selected_ids:
            self.update_order_state(order_id, reviewed=reviewed)
        self.populate_orders()
        current = self.tree_orders.selection()
        if current:
            self.on_order_select(None)

    def mark_selected_reviewed(self):
        self.set_reviewed_for_selected_orders(True)

    def mark_selected_unreviewed(self):
        self.set_reviewed_for_selected_orders(False)

    def get_order_by_id(self, order_id: str):
        for order in self.orders:
            if order.id == str(order_id):
                return order
        return None

    def open_db_dialog(self):
        if not HAS_MYSQL:
            messagebox.showerror("Missing Library", "Please run: pip install mysql-connector-python")
            return

        preset_name = self.get_selected_preset_name()
        preset = self.get_selected_preset()

        top = tk.Toplevel(self.root)
        top.title(f"Live Sytist Connection - {preset_name}")
        top.geometry("360x370")
        top.transient(self.root)
        top.grab_set()

        ttk.Label(top, text=f"Preset: {preset_name}").pack(pady=(10, 4))
        ttk.Label(top, text=f"Domain: {self.domain_var.get().strip()}").pack(pady=(0, 8))

        ttk.Label(top, text="Host IP:").pack(pady=2)
        host_entry = ttk.Entry(top)
        host_entry.insert(0, preset.get("host", ""))
        host_entry.pack(fill=tk.X, padx=20)

        ttk.Label(top, text="Database Name:").pack(pady=2)
        db_entry = ttk.Entry(top)
        db_entry.insert(0, preset.get("db_name", ""))
        db_entry.pack(fill=tk.X, padx=20)

        ttk.Label(top, text="Username (Read-Only):").pack(pady=2)
        user_entry = ttk.Entry(top)
        user_entry.insert(0, preset.get("db_user", ""))
        user_entry.pack(fill=tk.X, padx=20)

        # Load password from the OS keyring; fall back to in-memory value (which
        # is never written to disk — see ConfigStore.save).
        saved_pass = self._keyring_get(preset_name) or preset.get("db_pass", "")
        ttk.Label(top, text="Password:").pack(pady=2)
        pass_entry = ttk.Entry(top, show="*")
        pass_entry.insert(0, saved_pass)
        pass_entry.pack(fill=tk.X, padx=20)

        if HAS_KEYRING:
            ttk.Label(top, text="Password stored in OS keyring.", foreground="gray").pack(pady=(0, 2))
        else:
            ttk.Label(
                top,
                text="Install 'keyring' to store the password securely.",
                foreground="orange",
            ).pack(pady=(0, 2))

        def connect_live():
            try:
                password = pass_entry.get()
                domain = self.domain_var.get().strip()
                self.ensure_domain_in_favorites(domain)
                self.config["domain"] = domain
                self.config.setdefault("db_presets", {})[preset_name] = {
                    "domain": domain,
                    "host": host_entry.get().strip(),
                    "db_name": db_entry.get().strip(),
                    "db_user": user_entry.get().strip(),
                    # Keep db_pass in memory so the dialog can pre-fill it next
                    # time, but it will NOT be written to JSON by ConfigStore.save.
                    "db_pass": password,
                }
                self.config["selected_preset"] = preset_name
                # Persist password to OS keyring (if available).
                self._keyring_set(preset_name, password)
                self.save_config()

                orders, cart_items, photo_paths, status_lookup = self.data_loader.load_live_db(
                    host=host_entry.get().strip(),
                    user=user_entry.get().strip(),
                    password=password,
                    database=db_entry.get().strip(),
                )
                self.set_data(orders, cart_items, photo_paths, status_lookup)
                top.destroy()
                messagebox.showinfo("Success", f"Live connected! Fetched {len(self.orders)} orders.")
            except Exception as e:
                messagebox.showerror("Connection Error", str(e))

        ttk.Button(top, text="Connect & Fetch safely", command=connect_live).pack(pady=15)
        self.root.wait_window(top)

    def build_accounting_rows(self, order: Order):
        return [
            ("Subtotal", self.currency(order.subtotal)),
            ("Discount", self.currency(order.discount)),
            ("Shipping", self.currency(order.shipping)),
            ("Ship Cost", self.currency(order.ship_cost)),
            ("Tax", self.currency(order.tax)),
            ("Taxable Amount", self.currency(order.taxable_amount)),
            ("Tax %", str(order.tax_percentage or "")),
            ("VAT", self.currency(order.vat)),
            ("VAT %", str(order.vat_percentage or "")),
            ("Fees", self.currency(order.fees)),
            ("Order Fee", self.currency(order.order_fee)),
            ("Order Fee Name", order.order_fee_name or ""),
            ("Payment Fee", self.currency(order.payment_fee)),
            ("Payment Fee Name", order.payment_fee_name or ""),
            ("Credit", self.currency(order.credit)),
            ("Gift Certificate", self.currency(order.gift_certificate)),
            ("Payment Amount", self.currency(order.payment_amount)),
            ("Grand Total", self.currency(order.total)),
        ]

    def build_order_items(self, order_id: str):
        items = []
        domain = self.domain_var.get().rstrip('/')
        for item in self.cart_items:
            if item.order_id == order_id:
                url = ""
                photo = self.photo_paths.get(str(item.pic_id))
                if photo:
                    candidates = self.export_service._photo_url_candidates(domain, photo)
                    if candidates:
                        url = candidates[0]
                items.append((item, url))
        return items

    def open_order_detail_window(self, order_id: str):
        order = self.get_order_by_id(order_id)
        if not order:
            return
        rec = self.reconcile_order(order)
        state = self.get_order_state(order.id)
        self.populate_order_items(order.id)

        top = tk.Toplevel(self.root)
        top.title(f"Order Detail - {order.id}")
        top.geometry("1120x840")
        top.transient(self.root)

        outer = ttk.Frame(top, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(header, text=f"Order {order.id}", font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, text=f"Sytist: {order.status_name or order.status_id or 'Unknown'}").pack(side=tk.LEFT, padx=20)
        ttk.Label(header, text=f"Dashboard: {rec['dashboard_status']}").pack(side=tk.LEFT, padx=10)

        status_frame = ttk.LabelFrame(outer, text="Reconciliation", padding=10)
        status_frame.pack(fill=tk.X, pady=6)
        ttk.Label(status_frame, text=f"Sytist status: {order.status_name or order.status_id or 'Unknown'}").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Label(status_frame, text=f"Payment status: {order.payment_status or ''}").grid(row=0, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(status_frame, text="Dashboard status:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        dash_status_var = tk.StringVar(value=state.get("dashboard_status", "New"))
        dash_combo = ttk.Combobox(status_frame, textvariable=dash_status_var, values=DASHBOARD_STATUSES, state="readonly", width=24)
        dash_combo.grid(row=1, column=1, sticky="w", padx=6, pady=4)

        flagged_var = tk.BooleanVar(value=bool(state.get("flagged", False)))
        reviewed_var = tk.BooleanVar(value=bool(state.get("reviewed", False)))
        ttk.Checkbutton(status_frame, text="Flagged", variable=flagged_var).grid(row=1, column=2, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(status_frame, text="Reviewed", variable=reviewed_var).grid(row=1, column=3, sticky="w", padx=6, pady=4)

        ttk.Label(status_frame, text="Issues:").grid(row=2, column=0, sticky="nw", padx=6, pady=4)
        issues_box = tk.Text(status_frame, height=4, width=90)
        issues_box.grid(row=2, column=1, columnspan=3, sticky="ew", padx=6, pady=4)
        issues_text = "\n".join(rec["issues"]) if rec["issues"] else "No discrepancies detected."
        issues_box.insert("1.0", issues_text)
        issues_box.config(state="disabled")

        ttk.Label(status_frame, text="Dashboard notes:").grid(row=3, column=0, sticky="nw", padx=6, pady=4)
        notes_box = tk.Text(status_frame, height=5, width=90)
        notes_box.grid(row=3, column=1, columnspan=3, sticky="ew", padx=6, pady=4)
        notes_box.insert("1.0", state.get("notes", ""))

        def save_dashboard_fields():
            self.update_order_state(
                order.id,
                dashboard_status=dash_status_var.get(),
                reviewed=bool(reviewed_var.get()),
                flagged=bool(flagged_var.get()),
                notes=notes_box.get("1.0", "end").strip(),
                last_seen_sytist_status_id=order.status_id,
                last_seen_sytist_status_name=order.status_name,
                last_seen_payment_status=order.payment_status,
            )
            self.populate_orders()
            messagebox.showinfo("Saved", f"Saved dashboard state for order {order.id}.", parent=top)

        ttk.Button(status_frame, text="Save Dashboard Status", command=save_dashboard_fields).grid(row=4, column=3, sticky="e", padx=6, pady=6)
        status_frame.columnconfigure(1, weight=1)

        body = ttk.PanedWindow(outer, orient=tk.VERTICAL)
        body.pack(fill=tk.BOTH, expand=True, pady=6)

        top_half = ttk.PanedWindow(body, orient=tk.HORIZONTAL)
        body.add(top_half, weight=2)
        bottom_half = ttk.PanedWindow(body, orient=tk.HORIZONTAL)
        body.add(bottom_half, weight=2)

        customer = ttk.LabelFrame(top_half, text="Customer / Shipping", padding=10)
        top_half.add(customer, weight=1)
        accounting = ttk.LabelFrame(top_half, text="Accounting", padding=10)
        top_half.add(accounting, weight=1)

        payment = ttk.LabelFrame(bottom_half, text="Payment", padding=10)
        bottom_half.add(payment, weight=1)
        items = ttk.LabelFrame(bottom_half, text="Items", padding=10)
        bottom_half.add(items, weight=1)

        customer_rows = [
            ("Date", order.date),
            ("Name", order.name),
            ("Email", order.email),
            ("Phone", order.phone),
            ("Billing Address", ", ".join(filter(None, [order.address, order.address_2, order.city, order.state, order.zip_code, order.country]))),
            ("Shipping To", ", ".join(filter(None, [f"{order.ship_first_name} {order.ship_last_name}".strip(), order.ship_address, order.ship_address_2, order.ship_city, order.ship_state, order.ship_zip, order.ship_country]))),
            ("Shipping Option", order.shipping_option),
            ("Shipped By", order.shipped_by),
            ("Shipped Date", order.shipped_date),
            ("Tracking", order.shipped_track),
        ]
        for row, (label, value) in enumerate(customer_rows):
            ttk.Label(customer, text=f"{label}:", width=18).grid(row=row, column=0, sticky="nw", padx=4, pady=3)
            ttk.Label(customer, text=value or "", wraplength=420, justify=tk.LEFT).grid(row=row, column=1, sticky="w", padx=4, pady=3)
        ttk.Label(customer, text="Customer Notes:").grid(row=len(customer_rows), column=0, sticky="nw", padx=4, pady=3)
        cust_notes = tk.Text(customer, height=5, width=52)
        cust_notes.grid(row=len(customer_rows), column=1, sticky="ew", padx=4, pady=3)
        cust_notes.insert("1.0", order.customer_notes or "")
        cust_notes.config(state="disabled")
        ttk.Label(customer, text="Admin Notes:").grid(row=len(customer_rows)+1, column=0, sticky="nw", padx=4, pady=3)
        admin_notes = tk.Text(customer, height=5, width=52)
        admin_notes.grid(row=len(customer_rows)+1, column=1, sticky="ew", padx=4, pady=3)
        admin_notes.insert("1.0", order.admin_notes or "")
        admin_notes.config(state="disabled")
        customer.columnconfigure(1, weight=1)

        for row, (label, value) in enumerate(self.build_accounting_rows(order)):
            ttk.Label(accounting, text=f"{label}:", width=18).grid(row=row, column=0, sticky="w", padx=4, pady=3)
            ttk.Label(accounting, text=value or "").grid(row=row, column=1, sticky="e", padx=4, pady=3)
        accounting.columnconfigure(1, weight=1)

        payment_rows = [
            ("Payment Type", order.payment_type),
            ("Payment Status", order.payment_status),
            ("Transaction ID", order.payment_transaction),
            ("Reference", order.payment_reference),
            ("Payment Date", order.payment_date),
            ("Card Last Four", order.card_last_four),
            ("Short URL", order.short_url),
        ]
        for row, (label, value) in enumerate(payment_rows):
            ttk.Label(payment, text=f"{label}:", width=18).grid(row=row, column=0, sticky="nw", padx=4, pady=3)
            ttk.Label(payment, text=value or "", wraplength=420, justify=tk.LEFT).grid(row=row, column=1, sticky="w", padx=4, pady=3)

        paypal_url = self.paypal_transaction_url(order)
        ttk.Label(payment, text="PayPal Link:").grid(row=len(payment_rows), column=0, sticky="nw", padx=4, pady=3)
        link_label = ttk.Label(payment, text=paypal_url or "", foreground="blue", cursor="hand2", wraplength=420, justify=tk.LEFT)
        link_label.grid(row=len(payment_rows), column=1, sticky="w", padx=4, pady=3)
        if paypal_url:
            link_label.bind("<Button-1>", lambda e: webbrowser.open(paypal_url))

        ttk.Label(payment, text="Payment Info:").grid(row=len(payment_rows)+1, column=0, sticky="nw", padx=4, pady=3)
        pay_info = tk.Text(payment, height=10, width=52)
        pay_info.grid(row=len(payment_rows)+1, column=1, sticky="ew", padx=4, pady=3)
        pay_info.insert("1.0", order.payment_info or "")
        pay_info.config(state="disabled")
        payment.columnconfigure(1, weight=1)

        item_tree = ttk.Treeview(items, columns=("Product", "Qty", "Price", "File"), show="headings", height=16)
        for col, heading, width in [("Product", "Product", 170), ("Qty", "Qty", 50), ("Price", "Price", 70), ("File", "File", 220)]:
            item_tree.heading(col, text=heading)
            item_tree.column(col, width=width, anchor=tk.W if col in {"Product", "File"} else tk.E)
        item_tree.pack(fill=tk.BOTH, expand=True)

        item_urls = self.build_order_items(order.id)
        for item, url in item_urls:
            item_tree.insert("", tk.END, values=(item.product, item.qty, item.price, item.file))

        btn_row = ttk.Frame(items)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btn_row, text="Preview Selected Image", command=lambda: self.preview_selected_detail_item(item_tree, item_urls)).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="Close", command=top.destroy).pack(side=tk.RIGHT, padx=4)

    def preview_selected_detail_item(self, item_tree, item_urls):
        selected = item_tree.selection()
        if not selected:
            return
        index = item_tree.index(selected[0])
        if 0 <= index < len(item_urls):
            _, url = item_urls[index]
            if url:
                self.load_preview_image(url)

    def generate_print_folders(self):
        selected_orders = [order for order in self.orders if order.selected]
        if not selected_orders:
            messagebox.showwarning("No Orders", "Please select at least one order.")
            return

        base_dir = filedialog.askdirectory(title="Select Destination Folder")
        if not base_dir:
            return

        self.save_current_domain_to_selected_preset()
        self.save_config()

        # Capture the domain on the main thread before handing off to a worker.
        domain = self.domain_var.get().rstrip('/')

        # Build the progress window here (main thread) so Tkinter stays happy.
        prog_win = tk.Toplevel(self.root)
        prog_win.title("Processing Orders")
        prog_win.geometry("400x150")
        ttk.Label(prog_win, text="Downloading and Packing Images...").pack(pady=10)
        progress_var = tk.DoubleVar()
        ttk.Progressbar(prog_win, variable=progress_var, maximum=100).pack(fill=tk.X, padx=20, pady=10)
        status_label = ttk.Label(prog_win, text="Starting...")
        status_label.pack()

        threading.Thread(
            target=self._download_worker,
            args=(selected_orders, base_dir, domain, prog_win, status_label, progress_var),
            daemon=True,
        ).start()

    def _download_worker(self, selected_orders, base_dir, domain, prog_win, status_label, progress_var):
        """Background thread: download photos and update the progress window via after()."""
        tasks = self.export_service.build_download_tasks(
            selected_orders=selected_orders,
            cart_items=self.cart_items,
            photo_paths=self.photo_paths,
            domain=domain,
        )

        if not tasks:
            self.root.after(0, lambda: status_label.config(text="No valid items to download."))
            return

        def progress_callback(index, total, task):
            name = task.name_base
            pct = (index / total) * 100
            self.root.after(0, lambda n=name, p=pct: (
                status_label.config(text=f"Downloading {n}..."),
                progress_var.set(p),
            ))

        failed: list = []

        def error_callback(task, exc):
            logger.warning("Failed to download %s: %s", task.urls[0] if task.urls else "(no-url)", exc)
            failed.append((task, exc))

        self.export_service.process_downloads(
            tasks=tasks,
            base_dir=base_dir,
            progress_callback=progress_callback,
            error_callback=error_callback,
        )

        def _finish():
            if failed:
                first_err = str(failed[0][1])
                msg = f"Done with {len(failed)} error(s).\nFirst error: {first_err[:120]}"
            else:
                msg = "Done! You can import to Lightroom."
            status_label.config(text=msg)
            ttk.Button(prog_win, text="Close", command=prog_win.destroy).pack(pady=10)

        self.root.after(0, _finish)

    def build_order_print_jobs(self, selected_orders):
        jobs = []
        domain = self.domain_var.get().rstrip('/')
        for order in selected_orders:
            items = [i for i in self.cart_items if i.order_id == order.id and _safe_qty(i.qty) > 0]
            for item in items:
                photo = self.photo_paths.get(str(item.pic_id))
                if not photo:
                    continue
                candidates = self.export_service._photo_url_candidates(domain, photo)
                if not candidates:
                    continue
                url = candidates[0]
                size_key = self.printing_service.detect_size_key_for_order_item(item)
                qty = max(1, int(_safe_qty(item.qty)))
                for _ in range(qty):
                    jobs.append(PrintJob(
                        source_type="url",
                        source=url,
                        display_name=item.file or "photo",
                        product=item.product,
                        size_key=size_key,
                    ))
        return jobs

    def print_selected_orders(self):
        if not HAS_WIN32 or not HAS_PIL:
            messagebox.showerror("Missing Library", "Please run: pip install pywin32 pillow")
            return

        selected_orders = [order for order in self.orders if order.selected]
        if not selected_orders:
            messagebox.showwarning("No Orders", "Please select at least one order.")
            return

        jobs = self.build_order_print_jobs(selected_orders)
        self.start_print_workflow(jobs, "Order Print")

    def build_file_print_jobs(self, filepaths, chosen_type):
        jobs = []
        for filepath in filepaths:
            size_key = self.printing_service.detect_size_key_for_filepath(filepath) if chosen_type == "AUTO" else chosen_type
            jobs.append(PrintJob(
                source_type="file",
                source=filepath,
                display_name=os.path.basename(filepath),
                product=os.path.basename(filepath),
                size_key=size_key,
            ))
        return jobs

    def print_image_files(self):
        if not HAS_WIN32 or not HAS_PIL:
            messagebox.showerror("Missing Library", "Please run: pip install pywin32 pillow")
            return

        chosen_type = self.dialogs.ask_image_print_type()
        if not chosen_type:
            return

        filepaths = filedialog.askopenfilenames(
            title="Select Image Files to Print",
            filetypes=[
                ("Image Files", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"),
                ("All Files", "*.*"),
            ],
        )
        if not filepaths:
            return

        jobs = self.build_file_print_jobs(filepaths, chosen_type)
        self.start_print_workflow(jobs, "Image File Print")

    def open_button_print_editor(self):
        if not HAS_PIL or Image is None or ImageTk is None:
            messagebox.showerror("Missing Library", "Please run 'pip install pillow' to create button prints.")
            return

        filepath = filedialog.askopenfilename(
            title="Select Button Image",
            filetypes=[
                ("Image Files", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"),
                ("All Files", "*.*"),
            ],
        )
        if not filepath:
            return

        try:
            source_img = Image.open(filepath).convert("RGB")
        except Exception as exc:
            messagebox.showerror("Image Error", f"Could not open image:\n{exc}")
            return

        top = tk.Toplevel(self.root)
        top.title("Create Button Print")
        top.geometry("980x820")
        top.transient(self.root)

        state = {
            "source": source_img,
            "image_name": os.path.basename(filepath),
            "drag_start": None,
            "photo": None,
            "offset": [0, 0],
        }
        crop_w, crop_h = BUTTON_CROP_SIZE
        sheet_w, sheet_h = BUTTON_PRINT_SIZE
        initial_scale = max(crop_w / source_img.width, crop_h / source_img.height)
        state["scale"] = initial_scale
        resized_w = round(source_img.width * initial_scale)
        resized_h = round(source_img.height * initial_scale)
        state["offset"] = [
            round((crop_w - resized_w) / 2),
            round((crop_h - resized_h) / 2),
        ]

        ttk.Label(
            top,
            text="Drag the image inside the button circle. Adjust the template, finished button guide, and curved text before saving or printing.",
            wraplength=900,
            justify=tk.CENTER,
        ).pack(pady=(12, 6))

        editor_frame = ttk.Frame(top)
        editor_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        canvas_w, canvas_h = 420, 620
        page_x, page_y = 10, 10
        preview_w, preview_h = 400, 600
        preview_ratio = sheet_w / preview_w
        crop_preview_y = page_y + ((sheet_h - crop_h) // 2) / preview_ratio

        canvas = tk.Canvas(editor_frame, width=canvas_w, height=canvas_h, background="#d9d9d9", highlightthickness=0)
        canvas.pack(side=tk.LEFT, padx=(0, 14), pady=0)

        controls = ttk.Frame(editor_frame)
        controls.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        zoom_var = tk.DoubleVar(value=100)
        zoom_label = ttk.Label(controls, text="Zoom: 100%")
        zoom_label.pack(anchor="w")

        ttk.Scale(controls, from_=50, to=250, orient=tk.HORIZONTAL, variable=zoom_var).pack(fill=tk.X, pady=(0, 10))

        outer_diameter_var = tk.StringVar(value=str(BUTTON_DEFAULT_DIAMETER))
        finished_diameter_var = tk.StringVar(value=str(BUTTON_DEFAULT_FINISHED_DIAMETER))
        print_finished_var = tk.BooleanVar(value=True)
        print_lime_rect_var = tk.BooleanVar(value=False)
        lime_rect_width_var = tk.StringVar(value=str(BUTTON_PRINT_SIZE[0]))
        circle_offset_x_var = tk.StringVar(value="0")
        circle_offset_y_var = tk.StringVar(value="0")
        edge_border_var = tk.BooleanVar(value=False)
        print_params_var = tk.BooleanVar(value=False)
        text_var = tk.StringVar()
        position_var = tk.StringVar(value="top")
        facing_var = tk.StringVar(value="outward")
        font_size_var = tk.StringVar(value="72")
        text_color_var = tk.StringVar(value="#000000")
        text_style_var = tk.StringVar(value="Regular")
        char_spacing_var = tk.StringVar(value="0")
        radius_offset_var = tk.StringVar(value="0")
        stroke_color_var = tk.StringVar(value="#000000")
        stroke_width_var = tk.StringVar(value="0")
        try:
            font_values = sorted(tkfont.families(root=top))
        except Exception:
            font_values = []
        font_var = tk.StringVar(value=("Arial" if "Arial" in font_values else "DejaVuSans.ttf"))

        def choose_text_color():
            _, color = colorchooser.askcolor(color=text_color_var.get() or "#000000", parent=top)
            if color:
                text_color_var.set(color)

        def choose_stroke_color():
            _, color = colorchooser.askcolor(color=stroke_color_var.get() or "#000000", parent=top)
            if color:
                stroke_color_var.set(color)

        template_frame = ttk.LabelFrame(controls, text="Template circles", padding=8)
        template_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(template_frame, text="Outer circle diameter (px):").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Spinbox(template_frame, from_=50, to=min(BUTTON_CROP_SIZE), increment=1, textvariable=outer_diameter_var, width=10).grid(row=0, column=1, sticky="w", padx=6, pady=3)
        ttk.Label(template_frame, text="Finished red circle diameter (px):").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Spinbox(template_frame, from_=50, to=min(BUTTON_CROP_SIZE), increment=1, textvariable=finished_diameter_var, width=10).grid(row=1, column=1, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(template_frame, text="Print red finished-button circle", variable=print_finished_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(template_frame, text="Print lime green 2:3 calibration rectangle", variable=print_lime_rect_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Label(template_frame, text="Lime rectangle width (px):").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Spinbox(template_frame, from_=1, to=BUTTON_PRINT_SIZE[0], increment=1, textvariable=lime_rect_width_var, width=10).grid(row=4, column=1, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(template_frame, text="Yellow edge border around main circle", variable=edge_border_var).grid(row=5, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(template_frame, text="Print parameters on output", variable=print_params_var).grid(row=6, column=0, columnspan=2, sticky="w", pady=3)

        circle_pos_frame = ttk.LabelFrame(controls, text="Circle position (D-pad)", padding=8)
        circle_pos_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(circle_pos_frame, text="Offset X (px):").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Spinbox(circle_pos_frame, from_=-min(BUTTON_CROP_SIZE), to=min(BUTTON_CROP_SIZE), increment=1, textvariable=circle_offset_x_var, width=8).grid(row=0, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(circle_pos_frame, text="Offset Y (px):").grid(row=0, column=2, sticky="w", pady=2)
        ttk.Spinbox(circle_pos_frame, from_=-min(BUTTON_CROP_SIZE), to=min(BUTTON_CROP_SIZE), increment=1, textvariable=circle_offset_y_var, width=8).grid(row=0, column=3, sticky="w", padx=6, pady=2)

        dpad_frame = ttk.Frame(circle_pos_frame)
        dpad_frame.grid(row=1, column=0, columnspan=4, pady=4)
        ttk.Label(dpad_frame, text="Step (px):").grid(row=0, column=0, sticky="e", padx=(0, 4))
        dpad_step_var = tk.StringVar(value="10")
        ttk.Spinbox(dpad_frame, from_=1, to=200, increment=1, textvariable=dpad_step_var, width=6).grid(row=0, column=1, sticky="w")

        def _dpad_move(dx, dy):
            try:
                step = max(1, int(round(float(dpad_step_var.get()))))
            except (TypeError, ValueError):
                step = 10
            try:
                x = int(round(float(circle_offset_x_var.get())))
            except (TypeError, ValueError):
                x = 0
            try:
                y = int(round(float(circle_offset_y_var.get())))
            except (TypeError, ValueError):
                y = 0
            circle_offset_x_var.set(str(x + dx * step))
            circle_offset_y_var.set(str(y + dy * step))

        ttk.Button(dpad_frame, text="↑", width=3, command=lambda: _dpad_move(0, -1)).grid(row=1, column=2, padx=2, pady=1)
        ttk.Button(dpad_frame, text="←", width=3, command=lambda: _dpad_move(-1, 0)).grid(row=2, column=1, padx=2, pady=1)
        ttk.Button(dpad_frame, text="●", width=3, command=lambda: [circle_offset_x_var.set("0"), circle_offset_y_var.set("0")]).grid(row=2, column=2, padx=2, pady=1)
        ttk.Button(dpad_frame, text="→", width=3, command=lambda: _dpad_move(1, 0)).grid(row=2, column=3, padx=2, pady=1)
        ttk.Button(dpad_frame, text="↓", width=3, command=lambda: _dpad_move(0, 1)).grid(row=3, column=2, padx=2, pady=1)

        text_frame = ttk.LabelFrame(controls, text="Curved text", padding=8)
        text_frame.pack(fill=tk.X)
        ttk.Label(text_frame, text="Text:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(text_frame, textvariable=text_var, width=35).grid(row=0, column=1, columnspan=3, sticky="ew", padx=6, pady=3)
        ttk.Label(text_frame, text="Position:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(text_frame, textvariable=position_var, values=["top", "right", "bottom", "left"], state="readonly", width=12).grid(row=1, column=1, sticky="w", padx=6, pady=3)
        ttk.Label(text_frame, text="Facing:").grid(row=1, column=2, sticky="w", pady=3)
        ttk.Combobox(text_frame, textvariable=facing_var, values=["outward", "inward"], state="readonly", width=12).grid(row=1, column=3, sticky="w", padx=6, pady=3)
        ttk.Label(text_frame, text="Font:").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(text_frame, textvariable=font_var, values=font_values, width=30).grid(row=2, column=1, columnspan=3, sticky="ew", padx=6, pady=3)
        ttk.Label(text_frame, text="Size:").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Spinbox(text_frame, from_=6, to=240, increment=1, textvariable=font_size_var, width=8).grid(row=3, column=1, sticky="w", padx=6, pady=3)
        ttk.Label(text_frame, text="Color:").grid(row=3, column=2, sticky="w", pady=3)
        ttk.Entry(text_frame, textvariable=text_color_var, width=12).grid(row=3, column=3, sticky="w", padx=6, pady=3)
        ttk.Button(text_frame, text="Choose", command=choose_text_color).grid(row=3, column=4, sticky="w", pady=3)
        ttk.Label(text_frame, text="Style:").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Combobox(text_frame, textvariable=text_style_var, values=["Regular", "Bold", "Italic", "Bold Italic"], state="readonly", width=12).grid(row=4, column=1, sticky="w", padx=6, pady=3)
        ttk.Label(text_frame, text="Character spacing:").grid(row=4, column=2, sticky="w", pady=3)
        ttk.Spinbox(text_frame, from_=-20, to=80, increment=1, textvariable=char_spacing_var, width=8).grid(row=4, column=3, sticky="w", padx=6, pady=3)
        ttk.Label(text_frame, text="Text inset from edge:").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Spinbox(text_frame, from_=-100, to=400, increment=1, textvariable=radius_offset_var, width=8).grid(row=5, column=1, sticky="w", padx=6, pady=3)
        ttk.Label(text_frame, text="Stroke color:").grid(row=6, column=0, sticky="w", pady=3)
        ttk.Entry(text_frame, textvariable=stroke_color_var, width=12).grid(row=6, column=1, sticky="w", padx=6, pady=3)
        ttk.Button(text_frame, text="Choose", command=choose_stroke_color).grid(row=6, column=2, sticky="w", pady=3)
        ttk.Label(text_frame, text="Stroke width (px):").grid(row=7, column=0, sticky="w", pady=3)
        ttk.Spinbox(text_frame, from_=0, to=40, increment=1, textvariable=stroke_width_var, width=8).grid(row=7, column=1, sticky="w", padx=6, pady=3)
        text_frame.columnconfigure(1, weight=1)

        def render_current_sheet():
            try:
                cx_off = int(round(float(circle_offset_x_var.get())))
            except (TypeError, ValueError):
                cx_off = 0
            try:
                cy_off = int(round(float(circle_offset_y_var.get())))
            except (TypeError, ValueError):
                cy_off = 0
            return self.printing_service.render_button_sheet(
                state["source"],
                scale=state["scale"],
                offset=state["offset"],
                circle_diameter=outer_diameter_var.get(),
                finished_diameter=finished_diameter_var.get(),
                print_finished_circle=print_finished_var.get(),
                print_lime_calibration_rectangle=print_lime_rect_var.get(),
                lime_rectangle_width=lime_rect_width_var.get(),
                circle_offset=(cx_off, cy_off),
                edge_border=edge_border_var.get(),
                print_params=print_params_var.get(),
                curved_text={
                    "text": text_var.get(),
                    "position": position_var.get(),
                    "inward": facing_var.get() == "inward",
                    "font_family": font_var.get(),
                    "font_size": font_size_var.get(),
                    "color": text_color_var.get(),
                    "style": text_style_var.get(),
                    "char_spacing": char_spacing_var.get(),
                    "radius_offset": radius_offset_var.get(),
                    "stroke_color": stroke_color_var.get(),
                    "stroke_width": stroke_width_var.get(),
                },
            )

        def redraw():
            sheet = render_current_sheet()
            preview = sheet.resize((preview_w, preview_h), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(preview)
            state["photo"] = photo
            canvas.delete("all")
            canvas.create_rectangle(page_x - 1, page_y - 1, page_x + preview_w + 1, page_y + preview_h + 1, outline="#888")
            canvas.create_image(page_x, page_y, anchor=tk.NW, image=photo)
            try:
                outer_diameter = int(round(float(outer_diameter_var.get())))
            except (TypeError, ValueError):
                outer_diameter = BUTTON_DEFAULT_DIAMETER
            outer_diameter = max(50, min(outer_diameter, min(BUTTON_CROP_SIZE)))
            try:
                cx_off = int(round(float(circle_offset_x_var.get())))
                cy_off = int(round(float(circle_offset_y_var.get())))
            except (TypeError, ValueError):
                cx_off = cy_off = 0
            max_dx = (crop_w - outer_diameter) // 2
            max_dy = (crop_h - outer_diameter) // 2
            cx_off = max(-max_dx, min(cx_off, max_dx))
            cy_off = max(-max_dy, min(cy_off, max_dy))
            crop_preview_size = outer_diameter / preview_ratio
            crop_preview_x = page_x + ((crop_w - outer_diameter) / 2 + cx_off) / preview_ratio
            crop_preview_y_dynamic = crop_preview_y + ((crop_h - outer_diameter) / 2 + cy_off) / preview_ratio
            canvas.create_oval(
                crop_preview_x,
                crop_preview_y_dynamic,
                crop_preview_x + crop_preview_size,
                crop_preview_y_dynamic + crop_preview_size,
                outline="#111",
                width=2,
            )

        def set_zoom(value):
            pct = float(value)
            old_resized = (
                source_img.width * state["scale"],
                source_img.height * state["scale"],
            )
            center = (
                state["offset"][0] + old_resized[0] / 2,
                state["offset"][1] + old_resized[1] / 2,
            )
            state["scale"] = initial_scale * pct / 100
            new_resized = (
                source_img.width * state["scale"],
                source_img.height * state["scale"],
            )
            state["offset"] = [
                round(center[0] - new_resized[0] / 2),
                round(center[1] - new_resized[1] / 2),
            ]
            zoom_label.config(text=f"Zoom: {pct:.0f}%")
            redraw()

        zoom_var.trace_add("write", lambda *_: set_zoom(zoom_var.get()))
        for var in [
            outer_diameter_var,
            finished_diameter_var,
            print_finished_var,
            print_lime_rect_var,
            lime_rect_width_var,
            circle_offset_x_var,
            circle_offset_y_var,
            edge_border_var,
            print_params_var,
            text_var,
            position_var,
            facing_var,
            font_var,
            font_size_var,
            text_color_var,
            text_style_var,
            char_spacing_var,
            radius_offset_var,
            stroke_color_var,
            stroke_width_var,
        ]:
            var.trace_add("write", lambda *_: redraw())

        def on_drag_start(event):
            state["drag_start"] = (event.x, event.y)

        def on_drag(event):
            if not state["drag_start"]:
                return
            last_x, last_y = state["drag_start"]
            dx = round((event.x - last_x) * preview_ratio)
            dy = round((event.y - last_y) * preview_ratio)
            state["offset"][0] += dx
            state["offset"][1] += dy
            state["drag_start"] = (event.x, event.y)
            redraw()

        canvas.bind("<ButtonPress-1>", on_drag_start)
        canvas.bind("<B1-Motion>", on_drag)

        button_row = ttk.Frame(top)
        button_row.pack(fill=tk.X, padx=18, pady=12)

        def save_button_sheet():
            default_name = f"{os.path.splitext(state['image_name'])[0]}_button_4x6.png"
            save_path = filedialog.asksaveasfilename(
                title="Save Button 4x6 PNG",
                defaultextension=".png",
                initialfile=default_name,
                filetypes=[("PNG Image", "*.png"), ("All Files", "*.*")],
            )
            if not save_path:
                return
            try:
                render_current_sheet().save(save_path, format="PNG")
                messagebox.showinfo("Saved", f"Button print saved:\n{save_path}")
            except Exception as exc:
                messagebox.showerror("Save Error", f"Could not save button print:\n{exc}")

        def save_template():
            save_path = filedialog.asksaveasfilename(
                title="Save Button Template",
                defaultextension=".json",
                initialfile="button_template.json",
                filetypes=[("JSON Template", "*.json"), ("All Files", "*.*")],
            )
            if not save_path:
                return
            try:
                template = {
                    "outer_diameter": outer_diameter_var.get(),
                    "finished_diameter": finished_diameter_var.get(),
                    "print_finished_circle": print_finished_var.get(),
                    "print_lime_calibration_rectangle": print_lime_rect_var.get(),
                    "lime_rectangle_width": lime_rect_width_var.get(),
                    "circle_offset_x": circle_offset_x_var.get(),
                    "circle_offset_y": circle_offset_y_var.get(),
                    "edge_border": edge_border_var.get(),
                    "print_params": print_params_var.get(),
                    "text": text_var.get(),
                    "position": position_var.get(),
                    "facing": facing_var.get(),
                    "font": font_var.get(),
                    "font_size": font_size_var.get(),
                    "text_color": text_color_var.get(),
                    "text_style": text_style_var.get(),
                    "char_spacing": char_spacing_var.get(),
                    "radius_offset": radius_offset_var.get(),
                    "stroke_color": stroke_color_var.get(),
                    "stroke_width": stroke_width_var.get(),
                }
                import json as _json
                with open(save_path, "w", encoding="utf-8") as f:
                    _json.dump(template, f, indent=4)
                messagebox.showinfo("Saved", f"Template saved:\n{save_path}")
            except Exception as exc:
                messagebox.showerror("Save Error", f"Could not save template:\n{exc}")

        def load_template():
            load_path = filedialog.askopenfilename(
                title="Load Button Template",
                filetypes=[("JSON Template", "*.json"), ("All Files", "*.*")],
            )
            if not load_path:
                return
            try:
                import json as _json
                with open(load_path, "r", encoding="utf-8") as f:
                    template = _json.load(f)
                outer_diameter_var.set(str(template.get("outer_diameter", BUTTON_DEFAULT_DIAMETER)))
                finished_diameter_var.set(str(template.get("finished_diameter", BUTTON_DEFAULT_FINISHED_DIAMETER)))
                print_finished_var.set(bool(template.get("print_finished_circle", True)))
                print_lime_rect_var.set(bool(template.get("print_lime_calibration_rectangle", False)))
                lime_rect_width_var.set(str(template.get("lime_rectangle_width", BUTTON_PRINT_SIZE[0])))
                circle_offset_x_var.set(str(template.get("circle_offset_x", "0")))
                circle_offset_y_var.set(str(template.get("circle_offset_y", "0")))
                edge_border_var.set(bool(template.get("edge_border", False)))
                print_params_var.set(bool(template.get("print_params", False)))
                text_var.set(str(template.get("text", "")))
                position_var.set(str(template.get("position", "top")))
                facing_var.set(str(template.get("facing", "outward")))
                font_var.set(str(template.get("font", font_var.get())))
                font_size_var.set(str(template.get("font_size", "72")))
                text_color_var.set(str(template.get("text_color", "#000000")))
                text_style_var.set(str(template.get("text_style", "Regular")))
                char_spacing_var.set(str(template.get("char_spacing", "0")))
                radius_offset_var.set(str(template.get("radius_offset", "0")))
                stroke_color_var.set(str(template.get("stroke_color", "#000000")))
                stroke_width_var.set(str(template.get("stroke_width", "0")))
            except Exception as exc:
                messagebox.showerror("Load Error", f"Could not load template:\n{exc}")

        def print_button_sheet():
            if not HAS_WIN32:
                messagebox.showerror("Missing Library", "Please run: pip install pywin32")
                return
            job = PrintJob(
                source_type="pil",
                source=render_current_sheet(),
                display_name=f"Button - {state['image_name']}",
                product="Button",
                size_key="button",
            )
            self.start_print_workflow([job], "Button Print")

        ttk.Button(button_row, text="Save 4x6 PNG", command=save_button_sheet).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Print", command=print_button_sheet).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Save Template", command=save_template).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Load Template", command=load_template).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Close", command=top.destroy).pack(side=tk.RIGHT, padx=4)

        redraw()

    def get_address_prefill_order(self):
        selected_rows = self.tree_orders.selection()
        if selected_rows:
            order_id = str(self.tree_orders.item(selected_rows[0])["values"][1])
            return self.get_order_by_id(order_id)

        selected_orders = self.get_selected_orders()
        if len(selected_orders) == 1:
            return selected_orders[0]
        if len(selected_orders) > 1:
            messagebox.showinfo(
                "Manual Entry Required",
                "Multiple orders are checked, so the 4x6 address form will open blank. "
                "Select a single order row if you want to prefill an address.",
            )
        return None

    def open_address_print_dialog(self):
        if not HAS_WIN32 or not HAS_PIL:
            messagebox.showerror("Missing Library", "Please run: pip install pywin32 pillow")
            return

        order = self.get_address_prefill_order()
        default_address = self.build_order_shipping_address(order) if order else ShippingAddress(country="US")

        top = tk.Toplevel(self.root)
        top.title("Print 4x6 Address")
        top.geometry("760x360")
        top.transient(self.root)
        top.grab_set()

        frame = ttk.Frame(top, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        if order:
            heading = f"Address prefilled from order {order.id}. Edit any field before printing."
        else:
            heading = "Enter an address manually, or select one order first to prefill the form."
        ttk.Label(frame, text=heading, wraplength=700, justify=tk.LEFT).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

        fields = [
            ("Full Name", "full_name"),
            ("Address 1", "address_1"),
            ("Address 2", "address_2"),
            ("City", "city"),
            ("State", "state"),
            ("Postal Code", "postal_code"),
            ("Country", "country"),
        ]
        vars_map = {}
        for pos, (label, key) in enumerate(fields):
            row = 1 + (pos // 2)
            col = 0 if pos % 2 == 0 else 2
            ttk.Label(frame, text=f"{label}:").grid(row=row, column=col, sticky="w", padx=(0, 8), pady=4)
            value = str(getattr(default_address, key, "") or ("US" if key == "country" else ""))
            var = tk.StringVar(value=value)
            vars_map[key] = var
            ttk.Entry(frame, textvariable=var, width=28).grid(row=row, column=col + 1, sticky="ew", pady=4)

        preview_note = ttk.Label(
            frame,
            text="The address block prints centered on a 4x6 landscape card at 60% of the 6-inch width.",
            foreground="#666666",
            wraplength=700,
            justify=tk.LEFT,
        )
        preview_note.grid(row=5, column=0, columnspan=4, sticky="w", pady=(8, 0))

        for col in range(4):
            frame.columnconfigure(col, weight=1 if col in {1, 3} else 0)

        def build_address():
            return ShippingAddress(**{key: var.get().strip() for key, var in vars_map.items()})

        def print_address():
            address = build_address()
            required = {
                "Full Name": address.full_name,
                "Address 1": address.address_1,
                "City": address.city,
                "State": address.state,
                "Postal Code": address.postal_code,
            }
            missing = [label for label, value in required.items() if not str(value or "").strip()]
            if missing:
                messagebox.showwarning("Missing Address Fields", "Please fill: " + ", ".join(missing))
                return

            top.destroy()
            self.start_print_workflow(
                [
                    PrintJob(
                        source_type="address",
                        source=asdict(address),
                        display_name=(address.full_name or "address_label").strip(),
                        product="4x6 Address Label",
                        size_key="4x6",
                        address=address,
                    )
                ],
                "4x6 Address Print",
            )

        button_row = ttk.Frame(frame)
        button_row.grid(row=6, column=0, columnspan=4, sticky="e", pady=(16, 0))
        ttk.Button(button_row, text="Print", command=print_address).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Cancel", command=top.destroy).pack(side=tk.LEFT, padx=4)

    def start_print_workflow(self, jobs, title_text):
        if not jobs:
            messagebox.showwarning("No Jobs", "No print jobs were created.")
            return

        self.save_current_domain_to_selected_preset()
        self.save_config()
        all_resolved, unresolved, resolved_count = self.printing_service.analyze_jobs_for_routing(jobs)

        if not all_resolved:
            printers = self.printing_service.get_installed_printers()
            if not printers:
                messagebox.showerror("Error", "No printers found on this system.")
                return

            fallback_printer = self.dialogs.ask_fallback_printer(title_text, printers, unresolved, resolved_count)
            if not fallback_printer:
                messagebox.showerror("Error", "Please select a fallback printer.")
                return
        else:
            fallback_printer = None

        # Build the progress window on the main thread before spawning the worker.
        prog_win = tk.Toplevel(self.root)
        prog_win.title("Direct Print Spooler")
        prog_win.geometry("700x220")
        status_label = ttk.Label(prog_win, text="Preparing print jobs...")
        status_label.pack(padx=20, pady=10)
        detail_label = ttk.Label(prog_win, text="")
        detail_label.pack(padx=20, pady=5)
        result_label = ttk.Label(prog_win, text="")
        result_label.pack(padx=20, pady=5)

        threading.Thread(
            target=self._execute_print_jobs,
            args=(jobs, fallback_printer, prog_win, status_label, detail_label, result_label),
            daemon=True,
        ).start()

    def _execute_print_jobs(self, jobs, fallback_printer, prog_win, status_label, detail_label, result_label):
        """Background thread: execute print jobs and update the progress window via after()."""
        total = len(jobs)
        success_count = 0
        fail_count = 0

        for index, job in enumerate(jobs, start=1):
            size_key = job.size_key
            routed_printer = job.routed_printer or self.printing_service.get_routed_printer_for_key(size_key)
            target_printer = routed_printer if routed_printer else fallback_printer
            if not target_printer:
                fail_count += 1
                continue

            _idx, _name, _size, _printer = index, job.display_name, size_key or "UNKNOWN", target_printer
            self.root.after(0, lambda i=_idx, n=_name, s=_size, p=_printer: (
                status_label.config(text=f"Printing job {i} of {total}"),
                detail_label.config(text=f"{n} | size={s} | printer={p}"),
            ))

            try:
                self.printing_service.execute_print_job(job, fallback_printer)
                success_count += 1
            except Exception as e:
                fail_count += 1
                logger.warning("Failed to print %s on %s: %s", job.display_name, target_printer, e)

            _s, _f = success_count, fail_count
            self.root.after(0, lambda s=_s, f=_f: result_label.config(text=f"Sent: {s} Failed: {f}"))

        _s, _f, _t = success_count, fail_count, total

        def _finish():
            status_label.config(text="Printing complete.")
            detail_label.config(text="Check the Windows print queues for final spooler status.")
            result_label.config(text=f"Sent: {_s} of {_t} Failed: {_f}")
            ttk.Button(prog_win, text="Close", command=prog_win.destroy).pack(pady=10)

        self.root.after(0, _finish)

    def get_selected_orders(self):
        return [order for order in self.orders if getattr(order, "selected", False)]

    def get_shipping_target_order(self):
        selected_rows = self.tree_orders.selection()
        if selected_rows:
            order_id = str(self.tree_orders.item(selected_rows[0])["values"][1])
            order = self.get_order_by_id(order_id)
            if order:
                return order

        selected_orders = self.get_selected_orders()
        if len(selected_orders) == 1:
            return selected_orders[0]
        if len(selected_orders) > 1:
            messagebox.showinfo("Select One Order", "USPS shipping currently supports one order at a time.")
            return None
        messagebox.showwarning("No Order Selected", "Select an order in the table, or check one order first.")
        return None

    @staticmethod
    def build_order_shipping_address(order: Order):
        ship_first = (order.ship_first_name or "").strip()
        ship_last = (order.ship_last_name or "").strip()
        full_name = f"{ship_first} {ship_last}".strip() or order.name
        return ShippingAddress(
            full_name=full_name,
            address_1=(order.ship_address or order.address or "").strip(),
            address_2=(order.ship_address_2 or order.address_2 or "").strip(),
            city=(order.ship_city or order.city or "").strip(),
            state=(order.ship_state or order.state or "").strip(),
            postal_code=(order.ship_zip or order.zip_code or "").strip(),
            country=(order.ship_country or order.country or "US").strip() or "US",
            phone=(order.phone or "").strip(),
            email=(order.email or "").strip(),
        )

    def configure_usps(self):
        usps = self.config.setdefault("usps", {})
        ship_from = usps.setdefault("ship_from", {})

        top = tk.Toplevel(self.root)
        top.title("USPS Setup")
        top.geometry("660x600")
        top.transient(self.root)
        top.grab_set()

        frame = ttk.Frame(top, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        enabled_var = tk.BooleanVar(value=bool(usps.get("enabled", False)))
        ttk.Checkbutton(frame, text="Enable USPS integration", variable=enabled_var).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(
            frame,
            text="USPS cloud APIs require OAuth client credentials from developer.usps.com.\n"
                 "Secrets are stored in your local config file; do not commit them.",
            foreground="#666666",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))

        fields = [
            ("Environment", "environment", "production"),
            ("Base URL", "base_url", "https://api.usps.com"),
            ("Token URL (optional)", "token_url", ""),
            ("OAuth Client ID", "client_id", ""),
            ("OAuth Client Secret", "client_secret", ""),
            ("Timeout (seconds)", "timeout_seconds", "20"),
        ]
        vars_map = {}
        for idx, (label, key, default) in enumerate(fields, start=2):
            ttk.Label(frame, text=f"{label}:").grid(row=idx, column=0, sticky="w", padx=(0, 10), pady=4)
            var = tk.StringVar(value=str(usps.get(key, default) or default))
            vars_map[key] = var
            ttk.Entry(frame, textvariable=var, width=56).grid(row=idx, column=1, sticky="ew", pady=4)

        start_row = 2 + len(fields)
        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=start_row, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        ttk.Label(frame, text="Default Ship-From / Return Address").grid(row=start_row + 1, column=0, columnspan=2, sticky="w")

        ship_from_fields = [
            ("Name", "full_name"),
            ("Address 1", "address_1"),
            ("Address 2", "address_2"),
            ("City", "city"),
            ("State", "state"),
            ("ZIP", "postal_code"),
            ("Country", "country"),
            ("Phone", "phone"),
            ("Email", "email"),
        ]
        ship_from_vars = {}
        for idx, (label, key) in enumerate(ship_from_fields, start=start_row + 2):
            ttk.Label(frame, text=f"{label}:").grid(row=idx, column=0, sticky="w", padx=(0, 10), pady=3)
            var = tk.StringVar(value=str(ship_from.get(key, "") or ("US" if key == "country" else "")))
            ship_from_vars[key] = var
            ttk.Entry(frame, textvariable=var, width=56).grid(row=idx, column=1, sticky="ew", pady=3)

        frame.columnconfigure(1, weight=1)

        def save():
            usps["enabled"] = bool(enabled_var.get())
            for _, key, _ in fields:
                value = vars_map[key].get().strip()
                if key == "timeout_seconds":
                    try:
                        usps[key] = max(1, int(value or "20"))
                    except ValueError:
                        usps[key] = 20
                else:
                    usps[key] = value
            usps["ship_from"] = {key: var.get().strip() for key, var in ship_from_vars.items()}
            self.save_config()
            top.destroy()
            messagebox.showinfo("Saved", "USPS settings saved.")

        btns = ttk.Frame(frame)
        btns.grid(row=start_row + 2 + len(ship_from_fields), column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Save", command=save).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side=tk.LEFT, padx=6)

    def open_usps_shipping_dialog(self):
        order = self.get_shipping_target_order()
        if not order:
            return

        state = self.get_order_state(order.id)
        shipment = state.get("usps_shipment", {}) if isinstance(state.get("usps_shipment", {}), dict) else {}
        default_dest = self.build_order_shipping_address(order)
        saved_dest = shipment.get("destination", {}) if isinstance(shipment.get("destination", {}), dict) else {}
        saved_package = shipment.get("package", {}) if isinstance(shipment.get("package", {}), dict) else {}
        selected_rate_holder = {"value": shipment.get("selected_rate") if isinstance(shipment.get("selected_rate"), dict) else {}}

        top = tk.Toplevel(self.root)
        top.title(f"USPS Shipping - Order {order.id}")
        top.geometry("760x720")
        top.transient(self.root)
        top.grab_set()

        frame = ttk.Frame(top, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=f"Order {order.id} - {(order.name or '').strip()}").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        def _initial_value(key: str, fallback: str = ""):
            if key in saved_dest and str(saved_dest.get(key, "")).strip():
                return str(saved_dest.get(key, "")).strip()
            return str(getattr(default_dest, key, fallback) or fallback).strip()

        ttk.Label(frame, text="Destination").grid(row=1, column=0, columnspan=4, sticky="w")
        dest_fields = [
            ("Full Name", "full_name"),
            ("Address 1", "address_1"),
            ("Address 2", "address_2"),
            ("City", "city"),
            ("State", "state"),
            ("Postal Code", "postal_code"),
            ("Country", "country"),
            ("Phone", "phone"),
            ("Email", "email"),
        ]
        dest_vars = {}
        dest_start_row = 2
        for pos, (label, key) in enumerate(dest_fields):
            row = dest_start_row + (pos // 2)
            col = 0 if pos % 2 == 0 else 2
            ttk.Label(frame, text=f"{label}:").grid(row=row, column=col, sticky="w", padx=(0, 8), pady=3)
            var = tk.StringVar(value=_initial_value(key, "US" if key == "country" else ""))
            dest_vars[key] = var
            ttk.Entry(frame, textvariable=var, width=28).grid(row=row, column=col + 1, sticky="ew", pady=3)

        dest_end_row = dest_start_row + (len(dest_fields) - 1) // 2
        pkg_row = dest_end_row + 1
        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=pkg_row, column=0, columnspan=4, sticky="ew", pady=(12, 8))
        ttk.Label(frame, text="Package").grid(row=pkg_row + 1, column=0, sticky="w")
        pkg_fields = [
            ("Weight (oz)", "weight_oz"),
            ("Length (in)", "length_in"),
            ("Width (in)", "width_in"),
            ("Height (in)", "height_in"),
            ("Mail Class (optional)", "mail_class"),
        ]
        pkg_vars = {}
        for idx, (label, key) in enumerate(pkg_fields, start=pkg_row + 2):
            ttk.Label(frame, text=f"{label}:").grid(row=idx, column=0, sticky="w", padx=(0, 8), pady=3)
            var = tk.StringVar(value=str(saved_package.get(key, "") or ""))
            pkg_vars[key] = var
            ttk.Entry(frame, textvariable=var, width=28).grid(row=idx, column=1, sticky="ew", pady=3)

        tracking_row = pkg_row + 2 + len(pkg_fields)
        ttk.Label(frame, text="Tracking Number:").grid(row=tracking_row, column=0, sticky="w", pady=(8, 3))
        tracking_var = tk.StringVar(value=str(shipment.get("tracking_number", "") or order.shipped_track or ""))
        ttk.Entry(frame, textvariable=tracking_var, width=28).grid(row=tracking_row, column=1, sticky="ew", pady=(8, 3))

        output = tk.Text(frame, height=14, width=90)
        output.grid(row=tracking_row + 1, column=0, columnspan=4, sticky="nsew", pady=(10, 0))
        frame.rowconfigure(tracking_row + 1, weight=1)
        for col in range(4):
            frame.columnconfigure(col, weight=1 if col in {1, 3} else 0)

        def append_result(title: str, payload):
            output.insert(tk.END, f"\n=== {title} ===\n")
            if isinstance(payload, (dict, list)):
                output.insert(tk.END, json.dumps(payload, indent=2, default=str) + "\n")
            else:
                output.insert(tk.END, str(payload) + "\n")
            output.see(tk.END)

        def build_destination():
            return ShippingAddress(**{key: var.get().strip() for key, var in dest_vars.items()})

        def build_package():
            return PackageDetails(**{key: var.get().strip() for key, var in pkg_vars.items()})

        def save_shipment_metadata(extra=None):
            current = self.get_order_state(order.id).get("usps_shipment", {})
            if not isinstance(current, dict):
                current = {}
            payload = {
                "destination": asdict(build_destination()),
                "package": asdict(build_package()),
                "tracking_number": tracking_var.get().strip(),
                "selected_rate": selected_rate_holder["value"] if isinstance(selected_rate_holder["value"], dict) else {},
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            if extra:
                payload.update(extra)
            merged = dict(current)
            merged.update(payload)
            self.update_order_state(order.id, usps_shipment=merged)

        def run_action(action_name, action):
            try:
                result = action()
                append_result(action_name, result)
                return result
            except USPSNotConfiguredError as exc:
                messagebox.showerror("USPS Not Configured", str(exc))
                return None
            except USPSServiceError as exc:
                append_result(action_name, f"Error: {exc}")
                messagebox.showerror("USPS Error", str(exc))
                return None
            except Exception as exc:
                append_result(action_name, f"Unexpected error: {exc}")
                messagebox.showerror("USPS Error", str(exc))
                return None

        def validate_address():
            result = run_action("Validate Address", lambda: self.usps_service.validate_address(build_destination()))
            if result is not None:
                save_shipment_metadata({"last_validated_at": datetime.now().isoformat(timespec="seconds"), "last_error": ""})

        def fetch_rates():
            result = run_action(
                "Fetch Domestic Rates",
                lambda: self.usps_service.get_domestic_rates(build_destination(), build_package()),
            )
            if result is not None:
                candidate_rate = {}
                for key in ["selectedRate", "rate", "bestRate"]:
                    if isinstance(result.get(key), dict):
                        candidate_rate = result.get(key)
                        break
                if not candidate_rate:
                    for key in ["rates", "priceOptions", "options"]:
                        values = result.get(key)
                        if isinstance(values, list) and values and isinstance(values[0], dict):
                            candidate_rate = values[0]
                            break
                selected_rate_holder["value"] = candidate_rate
                save_shipment_metadata({"last_rated_at": datetime.now().isoformat(timespec="seconds"), "last_error": ""})

        def create_label():
            result = run_action(
                "Create Label",
                lambda: self.usps_service.create_label(
                    build_destination(),
                    build_package(),
                    selected_rate_holder["value"] if isinstance(selected_rate_holder["value"], dict) else {},
                ),
            )
            if result is not None:
                tracking = (
                    str(result.get("trackingNumber", "") or result.get("tracking_number", "")).strip()
                    or str((result.get("tracking") or {}).get("trackingNumber", "")).strip()
                )
                if tracking:
                    tracking_var.set(tracking)
                label_url = str(result.get("labelUrl", "") or result.get("label_url", "")).strip()
                label_format = str(result.get("labelFormat", "") or result.get("label_format", "")).strip()
                save_shipment_metadata(
                    {
                        "label": {"label_url": label_url, "label_format": label_format},
                        "tracking_number": tracking_var.get().strip(),
                        "label_created_at": datetime.now().isoformat(timespec="seconds"),
                        "last_error": "",
                    }
                )

        def track_package():
            tracking_number = tracking_var.get().strip()
            if not tracking_number:
                messagebox.showwarning("Missing Tracking Number", "Enter or create a tracking number first.")
                return
            result = run_action("Track Package", lambda: self.usps_service.get_tracking(tracking_number))
            if result is not None:
                save_shipment_metadata({"last_tracked_at": datetime.now().isoformat(timespec="seconds"), "last_error": ""})

        action_row = ttk.Frame(frame)
        action_row.grid(row=tracking_row + 2, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Button(action_row, text="Validate Address", command=validate_address).pack(side=tk.LEFT, padx=4)
        ttk.Button(action_row, text="Fetch Rates", command=fetch_rates).pack(side=tk.LEFT, padx=4)
        ttk.Button(action_row, text="Create Label", command=create_label).pack(side=tk.LEFT, padx=4)
        ttk.Button(action_row, text="Track Package", command=track_package).pack(side=tk.LEFT, padx=4)

        close_row = ttk.Frame(frame)
        close_row.grid(row=tracking_row + 3, column=0, columnspan=4, sticky="e", pady=(8, 0))
        ttk.Button(close_row, text="Save & Close", command=lambda: (save_shipment_metadata(), top.destroy())).pack(side=tk.LEFT, padx=4)
        ttk.Button(close_row, text="Cancel", command=top.destroy).pack(side=tk.LEFT, padx=4)

    def configure_zoho(self):
        preset_name = self.get_selected_preset_name() or "Default"
        preset = self.get_selected_preset()
        if not preset:
            messagebox.showerror("No Preset", "Select or save a preset/domain first.")
            return

        top = tk.Toplevel(self.root)
        top.title(f"Zoho Setup - {preset_name}")
        top.geometry("520x420")
        top.transient(self.root)
        top.grab_set()

        fields = [
            ("Accounts Domain", "zoho_accounts_domain", "https://accounts.zoho.com"),
            ("API Domain", "zoho_api_domain", "https://www.zohoapis.com"),
            ("Client ID", "zoho_client_id", ""),
            ("Client Secret", "zoho_client_secret", ""),
            ("Refresh Token", "zoho_refresh_token", ""),
            ("Organization ID", "zoho_organization_id", ""),
            ("Invoice Prefix", "zoho_prefix", ""),
        ]
        vars_map = {}
        frame = ttk.Frame(top, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Minimal Zoho Books invoice setup for the selected Sytist preset.").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        for idx, (label, key, default) in enumerate(fields, start=1):
            ttk.Label(frame, text=label + ":").grid(row=idx, column=0, sticky="w", padx=(0, 10), pady=6)
            var = tk.StringVar(value=str(preset.get(key, default) or default))
            vars_map[key] = var
            ttk.Entry(frame, textvariable=var, width=48).grid(row=idx, column=1, sticky="ew", pady=6)
        frame.columnconfigure(1, weight=1)

        def save():
            for _, key, _ in fields:
                preset[key] = vars_map[key].get().strip()
            self.save_config()
            top.destroy()
            messagebox.showinfo("Saved", f"Zoho settings saved for preset {preset_name}.")

        btns = ttk.Frame(frame)
        btns.grid(row=len(fields)+1, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Save", command=save).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side=tk.LEFT, padx=6)

    def get_zoho_client(self):
        preset = self.get_selected_preset() or {}
        required = {
            "zoho_accounts_domain": preset.get("zoho_accounts_domain", "").strip(),
            "zoho_api_domain": preset.get("zoho_api_domain", "").strip(),
            "zoho_client_id": preset.get("zoho_client_id", "").strip(),
            "zoho_client_secret": preset.get("zoho_client_secret", "").strip(),
            "zoho_refresh_token": preset.get("zoho_refresh_token", "").strip(),
            "zoho_organization_id": str(preset.get("zoho_organization_id", "")).strip(),
            "zoho_prefix": preset.get("zoho_prefix", "").strip(),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ZohoBooksError("Missing Zoho settings for selected preset: " + ", ".join(missing))
        return ZohoBooksClient(
            accounts_domain=required["zoho_accounts_domain"],
            client_id=required["zoho_client_id"],
            client_secret=required["zoho_client_secret"],
            refresh_token=required["zoho_refresh_token"],
            organization_id=required["zoho_organization_id"],
            api_domain=required["zoho_api_domain"],
        ), required["zoho_prefix"]

    def _order_items_for_order(self, order_id):
        return [item for item in self.cart_items if item.order_id == order_id and item.product]

    def _sanitize_zoho_text(self, value):
        text = str(value or "")
        return text.replace("<", "(").replace(">", ")").strip()

    def _build_zoho_invoice_payload(self, order, contact_id, invoice_number):
        items = self._order_items_for_order(order.id)
        line_items = []
        for item in items:
            qty = _safe_qty(item.qty) or 1
            try:
                rate = float(str(item.price or "0").replace("$", "").strip() or 0)
            except Exception:
                rate = 0.0
            name_parts = [self._sanitize_zoho_text(item.product or "")]
            if getattr(item, "file", ""):
                name_parts.append(self._sanitize_zoho_text(f"File: {item.file}"))
            description = " | ".join(part for part in name_parts if part)
            line_items.append({
                "name": self._sanitize_zoho_text(item.product or "Photo Order") or "Photo Order",
                "description": self._sanitize_zoho_text(description)[:2000],
                "quantity": qty,
                "rate": rate,
            })

        if not line_items:
            total_rate = float(str(order.total or "0").replace("$", "").strip() or 0)
            line_items.append({
                "name": self._sanitize_zoho_text(f"Sytist Order {order.id}"),
                "description": self._sanitize_zoho_text(f"Sytist order {order.id}"),
                "quantity": 1,
                "rate": total_rate,
            })

        return {
            "customer_id": contact_id,
            "invoice_number": self._sanitize_zoho_text(invoice_number),
            "reference_number": self._sanitize_zoho_text(str(order.id)),
            "date": (order.date or "")[:10],
            "notes": self._sanitize_zoho_text(f"Sytist order {order.id}"),
            "line_items": line_items,
        }

    def push_selected_to_zoho(self):
        selected = self.get_selected_orders()
        if not selected:
            messagebox.showinfo("No Orders Selected", "Select one or more orders with the checkbox first.")
            return
        try:
            client, prefix = self.get_zoho_client()
        except ZohoBooksError as exc:
            messagebox.showerror("Zoho Setup Incomplete", str(exc))
            return

        results = []
        for order in selected:
            try:
                invoice_number = client.build_invoice_number(prefix, order.id)
                existing = client.get_invoice_by_number(invoice_number)
                if existing:
                    self.update_order_state(
                        order.id,
                        zoho_invoice_id=str(existing.get("invoice_id", "")),
                        zoho_invoice_number=str(existing.get("invoice_number", invoice_number)),
                        zoho_last_push_at=datetime.now().isoformat(timespec="seconds"),
                        zoho_last_error="",
                    )
                    results.append(f"Order {order.id}: already exists as {existing.get('invoice_number', invoice_number)}")
                    continue

                contact = client.find_or_create_contact(order)
                contact_id = contact.get("contact_id")
                if not contact_id:
                    raise ZohoBooksError("Zoho contact create/lookup did not return contact_id.")

                payload = self._build_zoho_invoice_payload(order, contact_id, invoice_number)
                created = client.create_invoice(payload)
                invoice = created.get("invoice") or {}
                self.update_order_state(
                    order.id,
                    zoho_invoice_id=str(invoice.get("invoice_id", "")),
                    zoho_invoice_number=str(invoice.get("invoice_number", invoice_number)),
                    zoho_last_push_at=datetime.now().isoformat(timespec="seconds"),
                    zoho_last_error="",
                )
                results.append(f"Order {order.id}: created {invoice.get('invoice_number', invoice_number)}")
            except Exception as exc:
                self.update_order_state(order.id, zoho_last_error=str(exc))
                results.append(f"Order {order.id}: ERROR {exc}")

        self.populate_orders()
        messagebox.showinfo("Zoho Push Results", "\n".join(results[:25]))


if __name__ == "__main__":
    root = tk.Tk()
    app = SytistDashboard(root)
    root.mainloop()
