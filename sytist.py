import io
import os
import threading
import tkinter as tk
import urllib.parse
import urllib.request
import webbrowser
from decimal import Decimal, InvalidOperation
from tkinter import filedialog, messagebox, simpledialog, ttk

from config_store import ConfigStore
from dashboard_state import DashboardStateStore
from data_loader import HAS_MYSQL, SytistDataLoader
from dialogs import Dialogs
from export_service import ExportService
from models import CartItem, Order, PhotoPath, PrintJob
from printing_service import HAS_PIL, HAS_WIN32, PrintingService

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

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

        row1 = ttk.Frame(control_frame)
        row1.pack(fill=tk.X, pady=2)

        ttk.Button(row1, text="Load Offline .sql File", command=self.load_sql_file).pack(side=tk.LEFT, padx=5)
        ttk.Button(row1, text="Connect to Live DB", command=self.open_db_dialog).pack(side=tk.LEFT, padx=5)

        ttk.Separator(row1, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=15, fill=tk.Y)
        ttk.Button(row1, text="Generate Print Folders", command=self.generate_print_folders).pack(side=tk.LEFT, padx=5)

        ttk.Separator(row1, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=15, fill=tk.Y)
        ttk.Button(row1, text="Printer Routing", command=self.configure_printer_routing).pack(side=tk.LEFT, padx=5)
        ttk.Button(row1, text="Print Selected Orders", command=self.print_selected_orders).pack(side=tk.LEFT, padx=5)
        ttk.Button(row1, text="Print Image Files", command=self.print_image_files).pack(side=tk.LEFT, padx=5)

        ttk.Separator(row1, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=15, fill=tk.Y)
        ttk.Label(row1, text="Website / Favorite:").pack(side=tk.LEFT, padx=(5, 5))
        self.domain_var = tk.StringVar(value=self.config["domain"])
        self.domain_combo = ttk.Combobox(row1, textvariable=self.domain_var, width=35)
        self.domain_combo.pack(side=tk.LEFT, padx=5)
        self.domain_combo.bind("<<ComboboxSelected>>", self.on_domain_selected)
        ttk.Button(row1, text="Save Favorite", command=self.save_domain_as_favorite).pack(side=tk.LEFT, padx=5)

        row2 = ttk.Frame(control_frame)
        row2.pack(fill=tk.X, pady=(10, 2))
        ttk.Label(row2, text="Search Orders:").pack(side=tk.LEFT, padx=5)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.filter_orders)
        ttk.Entry(row2, textvariable=self.search_var, width=30).pack(side=tk.LEFT, padx=5)

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
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                raw_data = response.read()
            image = Image.open(io.BytesIO(raw_data))
            image.thumbnail((450, 450), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            self.root.after(0, lambda: self._update_preview_label(photo))
        except Exception as e:
            self.root.after(0, lambda: self.preview_label.config(text=f"Failed to load image.\n{e}", image=""))

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
        filepath = filedialog.askopenfilename(filetypes=[("SQL Files", "*.sql")])
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
                    url = f"{domain}/sy-photos/{photo.folder}/{photo.hashed_file}"
                self.tree_items.insert("", tk.END, values=(item.product, item.qty, item.price, item.file, url))

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
        top.geometry("360x330")
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

        ttk.Label(top, text="Password:").pack(pady=2)
        pass_entry = ttk.Entry(top, show="*")
        pass_entry.insert(0, preset.get("db_pass", ""))
        pass_entry.pack(fill=tk.X, padx=20)

        def connect_live():
            try:
                domain = self.domain_var.get().strip()
                self.ensure_domain_in_favorites(domain)
                self.config["domain"] = domain
                self.config.setdefault("db_presets", {})[preset_name] = {
                    "domain": domain,
                    "host": host_entry.get().strip(),
                    "db_name": db_entry.get().strip(),
                    "db_user": user_entry.get().strip(),
                    "db_pass": pass_entry.get(),
                }
                self.config["selected_preset"] = preset_name
                self.save_config()

                orders, cart_items, photo_paths, status_lookup = self.data_loader.load_live_db(
                    host=host_entry.get().strip(),
                    user=user_entry.get().strip(),
                    password=pass_entry.get(),
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
                    url = f"{domain}/sy-photos/{photo.folder}/{photo.hashed_file}"
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
        threading.Thread(target=self.process_downloads, args=(selected_orders, base_dir), daemon=True).start()

    def process_downloads(self, selected_orders, base_dir):
        prog_win = tk.Toplevel(self.root)
        prog_win.title("Processing Orders")
        prog_win.geometry("400x150")

        ttk.Label(prog_win, text="Downloading and Packing Images...").pack(pady=10)
        progress_var = tk.DoubleVar()
        ttk.Progressbar(prog_win, variable=progress_var, maximum=100).pack(fill=tk.X, padx=20, pady=10)
        status_label = ttk.Label(prog_win, text="Starting...")
        status_label.pack()

        domain = self.domain_var.get().rstrip('/')
        tasks = self.export_service.build_download_tasks(
            selected_orders=selected_orders,
            cart_items=self.cart_items,
            photo_paths=self.photo_paths,
            domain=domain,
        )

        if not tasks:
            status_label.config(text="No valid items to download.")
            return

        def progress_callback(index, total, task):
            status_label.config(text=f"Downloading {task.name_base}...")
            progress_var.set((index / total) * 100)
            self.root.update()

        def error_callback(task, exc):
            print(f"Failed to download {task.url}: {exc}")

        self.export_service.process_downloads(
            tasks=tasks,
            base_dir=base_dir,
            progress_callback=progress_callback,
            error_callback=error_callback,
        )

        status_label.config(text="Done! You can import to Lightroom.")
        ttk.Button(prog_win, text="Close", command=prog_win.destroy).pack(pady=10)

    def build_order_print_jobs(self, selected_orders):
        jobs = []
        domain = self.domain_var.get().rstrip('/')
        for order in selected_orders:
            items = [i for i in self.cart_items if i.order_id == order.id and float(i.qty) > 0]
            for item in items:
                photo = self.photo_paths.get(str(item.pic_id))
                if not photo:
                    continue
                url = f"{domain}/sy-photos/{photo.folder}/{photo.hashed_file}"
                size_key = self.printing_service.detect_size_key_for_order_item(item)
                qty = int(float(item.qty))
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

    def start_print_workflow(self, jobs, title_text):
        if not jobs:
            messagebox.showwarning("No Jobs", "No print jobs were created.")
            return

        self.save_current_domain_to_selected_preset()
        self.save_config()
        all_resolved, unresolved, resolved_count = self.printing_service.analyze_jobs_for_routing(jobs)

        if all_resolved:
            threading.Thread(target=self._execute_print_jobs, args=(jobs, None), daemon=True).start()
            return

        printers = self.printing_service.get_installed_printers()
        if not printers:
            messagebox.showerror("Error", "No printers found on this system.")
            return

        fallback_printer = self.dialogs.ask_fallback_printer(title_text, printers, unresolved, resolved_count)
        if not fallback_printer:
            messagebox.showerror("Error", "Please select a fallback printer.")
            return
        threading.Thread(target=self._execute_print_jobs, args=(jobs, fallback_printer), daemon=True).start()

    def _execute_print_jobs(self, jobs, fallback_printer):
        prog_win = tk.Toplevel(self.root)
        prog_win.title("Direct Print Spooler")
        prog_win.geometry("700x220")

        status_label = ttk.Label(prog_win, text="Preparing print jobs...")
        status_label.pack(padx=20, pady=10)
        detail_label = ttk.Label(prog_win, text="")
        detail_label.pack(padx=20, pady=5)
        result_label = ttk.Label(prog_win, text="")
        result_label.pack(padx=20, pady=5)

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

            status_label.config(text=f"Printing job {index} of {total}")
            detail_label.config(text=f"{job.display_name} | size={size_key or 'UNKNOWN'} | printer={target_printer}")
            self.root.update()

            try:
                self.printing_service.execute_print_job(job, fallback_printer)
                success_count += 1
            except Exception as e:
                fail_count += 1
                print(f"Failed to print {job.display_name} on {target_printer}: {e}")

            result_label.config(text=f"Sent: {success_count} Failed: {fail_count}")
            self.root.update()

        status_label.config(text="Printing complete.")
        detail_label.config(text="Check the Windows print queues for final spooler status.")
        result_label.config(text=f"Sent: {success_count} of {total} Failed: {fail_count}")
        ttk.Button(prog_win, text="Close", command=prog_win.destroy).pack(pady=10)


if __name__ == "__main__":
    root = tk.Tk()
    app = SytistDashboard(root)
    root.mainloop()
