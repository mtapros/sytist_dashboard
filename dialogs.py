import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from models import PrintJob


class Dialogs:
    def __init__(self, root: tk.Misc):
        self.root = root

    def show_printer_routing_dialog(
        self,
        current_routes: Dict[str, str],
        printers: List[str],
        on_save: Callable[[Dict[str, str]], None],
    ) -> None:
        top = tk.Toplevel(self.root)
        top.title("Printer Routing")
        top.geometry("760x390")
        top.transient(self.root)
        top.grab_set()

        ttk.Label(
            top,
            text="Assign a Windows printer queue for each print size/type.\n"
                 "Wallets may use their own route, or automatically fall back to the 5x7 route.",
        ).pack(pady=10)

        route_frame = ttk.Frame(top, padding=10)
        route_frame.pack(fill=tk.BOTH, expand=True)

        route_vars: Dict[str, tk.StringVar] = {}
        route_keys = [
            ("4x6", "4x6"),
            ("5x7", "5x7"),
            ("8x10", "8x10"),
            ("wallet", "Wallet"),
            ("button", "Button"),
            ("magnet", "Magnet"),
            ("7in", "7in Statuette"),
            ("10in", "10in Statuette"),
        ]
        values = [""] + printers
        for row, (key, label) in enumerate(route_keys):
            ttk.Label(route_frame, text=f"{label} queue:", width=18).grid(row=row, column=0, sticky="w", padx=5, pady=5)
            var = tk.StringVar(value=current_routes.get(key, ""))
            route_vars[key] = var
            combo = ttk.Combobox(route_frame, textvariable=var, values=values, width=65, state="readonly")
            combo.grid(row=row, column=1, sticky="ew", padx=5, pady=5)

        route_frame.columnconfigure(1, weight=1)

        def save_routes():
            on_save({key: var.get().strip() for key, var in route_vars.items()})
            top.destroy()

        ttk.Button(top, text="Save Routing", command=save_routes).pack(pady=10)
        self.root.wait_window(top)

    def ask_image_print_type(self) -> Optional[str]:
        top = tk.Toplevel(self.root)
        top.title("Choose Print Type")
        top.geometry("430x230")
        top.transient(self.root)
        top.grab_set()

        result = {"value": None}
        ttk.Label(top, text="How should the selected image files be printed?", justify=tk.CENTER).pack(pady=(20, 10))

        options = [
            ("Auto-detect from filename", "AUTO"),
            ("4x6", "4x6"),
            ("5x7", "5x7"),
            ("8x10", "8x10"),
            ("Wallets (2x2 on 5x7)", "wallet"),
            ("Button", "button"),
            ("Magnet", "magnet"),
            ("7in Statuette", "7in"),
            ("10in Statuette", "10in"),
        ]
        labels = [label for label, _ in options]
        value_map = {label: value for label, value in options}

        selection_var = tk.StringVar(value=labels[2])
        combo = ttk.Combobox(top, textvariable=selection_var, values=labels, state="readonly", width=32)
        combo.pack(pady=10)
        combo.current(2)

        def confirm():
            result["value"] = value_map[selection_var.get()]
            top.destroy()

        def cancel():
            result["value"] = None
            top.destroy()

        button_row = ttk.Frame(top)
        button_row.pack(pady=20)
        ttk.Button(button_row, text="OK", command=confirm).pack(side=tk.LEFT, padx=10)
        ttk.Button(button_row, text="Cancel", command=cancel).pack(side=tk.LEFT, padx=10)

        top.protocol("WM_DELETE_WINDOW", cancel)
        self.root.wait_window(top)
        return result["value"]

    def ask_fallback_printer(self, title_text: str, printers: List[str], unresolved: List[PrintJob], resolved_count: int) -> Optional[str]:
        top = tk.Toplevel(self.root)
        top.title(title_text)
        top.geometry("650x320")
        top.transient(self.root)
        top.grab_set()

        unknown_count = sum(1 for j in unresolved if not j.size_key)
        unrouted_known_count = sum(1 for j in unresolved if j.size_key)
        ttk.Label(
            top,
            text=f"{resolved_count} jobs already have routed queues.\n{len(unresolved)} jobs still need a fallback printer.",
        ).pack(pady=10)

        detail_lines = []
        if unknown_count:
            detail_lines.append(f"Unknown sizes: {unknown_count}")
        if unrouted_known_count:
            detail_lines.append(f"Known sizes with no route configured: {unrouted_known_count}")
        if detail_lines:
            ttk.Label(top, text=" | ".join(detail_lines)).pack(pady=2)

        ttk.Label(top, text="Fallback printer for unresolved jobs only:").pack(pady=(15, 5))
        printer_var = tk.StringVar()
        printer_combo = ttk.Combobox(top, textvariable=printer_var, values=printers, width=65, state="readonly")
        printer_combo.pack(pady=5)
        if printers:
            printer_combo.current(0)

        preview_text = []
        for job in unresolved[:8]:
            label = job.size_key or "UNKNOWN"
            preview_text.append(f"{label} -> {job.display_name}")
        if len(unresolved) > 8:
            preview_text.append(f"... plus {len(unresolved) - 8} more")

        preview_box = tk.Text(top, height=8, width=72)
        preview_box.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)
        preview_box.insert("1.0", "\n".join(preview_text))
        preview_box.config(state="disabled")

        result = {"value": None}

        def confirm():
            result["value"] = printer_var.get().strip() or None
            top.destroy()

        ttk.Button(top, text="Start Printing", command=confirm).pack(pady=10)
        self.root.wait_window(top)
        return result["value"]
