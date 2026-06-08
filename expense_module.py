"""Receipt expense extraction module.

This module provides a small Tkinter UI and a provider-agnostic client for
sending a JPG/JPEG receipt image to a vision-language (VL/VLM) endpoint and
asking it to return structured expense information.

The HTTP payload is intentionally OpenAI-compatible because many hosted and
local VL services support that format. Configure the endpoint, API key, and
model in the dialog, then customize up to 10 extraction fields.
"""

from __future__ import annotations

import base64
import csv
import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


DEFAULT_EXPENSE_FIELDS: list[dict[str, str]] = [
    {"name": "merchant_name", "description": "Store, restaurant, vendor, or merchant name."},
    {"name": "receipt_date", "description": "Transaction date in YYYY-MM-DD format if visible."},
    {"name": "receipt_time", "description": "Transaction time if visible."},
    {"name": "subtotal", "description": "Subtotal before tax, tip, discounts, or fees."},
    {"name": "tax", "description": "Total sales tax or VAT charged."},
    {"name": "tip", "description": "Tip or gratuity amount, if present."},
    {"name": "total", "description": "Final total paid."},
    {"name": "payment_method", "description": "Payment method, card brand, or last four digits if visible."},
    {"name": "items", "description": "Line items with name, quantity, and price when readable."},
    {"name": "expense_category", "description": "Best-fit category such as Meals, Supplies, Travel, Fuel, or Office."},
]


@dataclass
class ExpenseFieldSpec:
    """A user-configurable field requested from the VL model."""

    name: str
    description: str = ""

    def normalized_name(self) -> str:
        return normalize_field_name(self.name)


@dataclass
class ExpenseExtractionResult:
    """Structured output returned by a receipt extraction request."""

    image_path: str
    model: str
    fields: dict[str, Any]
    raw_response: dict[str, Any]
    extracted_at: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False, default=str)


class ExpenseVLClientError(Exception):
    """Raised when the VL endpoint cannot be called or parsed."""


class ExpenseVLClient:
    """Client for OpenAI-compatible vision-language chat-completions APIs."""

    def __init__(
        self,
        endpoint_url: str,
        api_key: str = "",
        model: str = "",
        timeout_seconds: int = 60,
    ) -> None:
        self.endpoint_url = (endpoint_url or "").strip()
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.timeout_seconds = max(1, int(timeout_seconds or 60))

    def extract_receipt(
        self,
        image_path: str,
        field_specs: Iterable[ExpenseFieldSpec | dict[str, str]],
        extra_instructions: str = "",
    ) -> ExpenseExtractionResult:
        if not self.endpoint_url:
            raise ExpenseVLClientError("VL endpoint URL is required.")
        if not self.model:
            raise ExpenseVLClientError("VL model name is required.")
        if not image_path or not os.path.exists(image_path):
            raise ExpenseVLClientError("Choose an existing JPG/JPEG receipt image first.")

        specs = normalize_field_specs(field_specs)
        if not specs:
            raise ExpenseVLClientError("At least one return field is required.")

        payload = self.build_payload(image_path, specs, extra_instructions=extra_instructions)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = urllib.request.Request(
            self.endpoint_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ExpenseVLClientError(f"VL endpoint returned HTTP {exc.code}: {body or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise ExpenseVLClientError(f"Could not reach VL endpoint: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ExpenseVLClientError("VL endpoint timed out.") from exc
        except Exception as exc:  # pragma: no cover - defensive wrapper
            raise ExpenseVLClientError(f"Unexpected VL endpoint error: {exc}") from exc

        try:
            raw_response = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ExpenseVLClientError(f"VL endpoint did not return JSON: {raw_body[:500]}") from exc

        fields = parse_vl_fields(raw_response)
        fields = coerce_requested_fields(fields, specs)
        return ExpenseExtractionResult(
            image_path=image_path,
            model=self.model,
            fields=fields,
            raw_response=raw_response,
            extracted_at=datetime.now().isoformat(timespec="seconds"),
        )

    def build_payload(
        self,
        image_path: str,
        field_specs: list[ExpenseFieldSpec],
        extra_instructions: str = "",
    ) -> dict[str, Any]:
        image_data = encode_jpg_as_data_url(image_path)
        requested_schema = {
            spec.normalized_name(): spec.description.strip() or f"Extract {spec.normalized_name()} from the receipt."
            for spec in field_specs
        }
        field_names = list(requested_schema.keys())
        system_prompt = (
            "You extract expense data from store receipt images. "
            "Return only strict JSON. Do not include markdown fences or commentary. "
            "Use null when a requested value is not visible. Preserve currency values as strings exactly as read when possible."
        )
        user_prompt = {
            "task": "Extract the requested receipt fields from this image.",
            "requested_fields": requested_schema,
            "response_format": {
                "fields": {name: "value or null" for name in field_names},
                "confidence": "0-1 overall confidence estimate",
                "notes": "brief uncertainty notes",
            },
            "extra_instructions": extra_instructions.strip(),
        }
        return {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": json.dumps(user_prompt, ensure_ascii=False)},
                        {"type": "image_url", "image_url": {"url": image_data}},
                    ],
                },
            ],
        }


def normalize_field_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(name or "").strip().lower())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def normalize_field_specs(field_specs: Iterable[ExpenseFieldSpec | dict[str, str]]) -> list[ExpenseFieldSpec]:
    normalized: list[ExpenseFieldSpec] = []
    seen: set[str] = set()
    for spec in field_specs:
        if isinstance(spec, ExpenseFieldSpec):
            name = spec.name
            description = spec.description
        else:
            name = str(spec.get("name", ""))
            description = str(spec.get("description", ""))
        normalized_name = normalize_field_name(name)
        if not normalized_name or normalized_name in seen:
            continue
        normalized.append(ExpenseFieldSpec(normalized_name, description.strip()))
        seen.add(normalized_name)
        if len(normalized) >= 10:
            break
    return normalized


def encode_jpg_as_data_url(image_path: str) -> str:
    extension = os.path.splitext(image_path)[1].lower()
    if extension not in {".jpg", ".jpeg"}:
        raise ExpenseVLClientError("Receipt image must be a .jpg or .jpeg file.")
    with open(image_path, "rb") as image_file:
        encoded = base64.b64encode(image_file.read()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def parse_vl_fields(raw_response: dict[str, Any]) -> dict[str, Any]:
    """Parse fields from either direct JSON or chat-completions JSON."""

    if isinstance(raw_response.get("fields"), dict):
        return dict(raw_response["fields"])

    content = None
    choices = raw_response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if isinstance(content, str) and content.strip():
        parsed = _loads_json_object_from_text(content)
        if isinstance(parsed.get("fields"), dict):
            return dict(parsed["fields"])
        if isinstance(parsed, dict):
            return parsed

    # Fallback for providers that return arbitrary JSON at the top level.
    return {key: value for key, value in raw_response.items() if key not in {"choices", "usage", "model"}}


def _loads_json_object_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(cleaned[start : end + 1])
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        raise


def coerce_requested_fields(fields: dict[str, Any], specs: list[ExpenseFieldSpec]) -> dict[str, Any]:
    coerced: dict[str, Any] = {}
    lower_lookup = {normalize_field_name(key): value for key, value in fields.items()}
    for spec in specs:
        key = spec.normalized_name()
        coerced[key] = lower_lookup.get(key)
    # Preserve useful metadata if the model returned it.
    for key in ["confidence", "notes"]:
        if key in fields and key not in coerced:
            coerced[key] = fields[key]
    return coerced


def default_expense_config() -> dict[str, Any]:
    return {
        "endpoint_url": "",
        "api_key": "",
        "model": "",
        "timeout_seconds": 60,
        "fields": DEFAULT_EXPENSE_FIELDS,
        "extra_instructions": "",
    }


class ExpenseReceiptDialog:
    """Tkinter dialog for receipt image upload and configurable extraction."""

    def __init__(self, parent: tk.Misc, config: dict[str, Any] | None = None, on_config_saved=None) -> None:
        self.parent = parent
        self.config = config if isinstance(config, dict) else {}
        self.expense_config = self.config.setdefault("expense_vl", default_expense_config())
        self.on_config_saved = on_config_saved
        self.image_path_var = tk.StringVar(value="")
        self.endpoint_var = tk.StringVar(value=str(self.expense_config.get("endpoint_url", "")))
        self.api_key_var = tk.StringVar(value=str(self.expense_config.get("api_key", "")))
        self.model_var = tk.StringVar(value=str(self.expense_config.get("model", "")))
        self.timeout_var = tk.StringVar(value=str(self.expense_config.get("timeout_seconds", 60)))
        self.extra_var = tk.StringVar(value=str(self.expense_config.get("extra_instructions", "")))
        self.field_vars: list[tuple[tk.StringVar, tk.StringVar]] = []
        self.last_result: ExpenseExtractionResult | None = None
        self.result_text: tk.Text | None = None
        self.analyze_button: ttk.Button | None = None

    def show(self) -> None:
        top = tk.Toplevel(self.parent)
        top.title("Expense Receipt VL Extraction")
        top.geometry("980x760")
        top.transient(self.parent)

        outer = ttk.Frame(top, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            outer,
            text="Load a JPG receipt, choose your VL endpoint/model, then customize up to 10 fields to extract.",
            wraplength=920,
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(0, 10))

        settings = ttk.LabelFrame(outer, text="VL settings", padding=10)
        settings.pack(fill=tk.X)
        ttk.Label(settings, text="Endpoint URL:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(settings, textvariable=self.endpoint_var, width=72).grid(row=0, column=1, columnspan=3, sticky="ew", pady=4)
        ttk.Label(settings, text="Model:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(settings, textvariable=self.model_var, width=28).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(settings, text="Timeout:").grid(row=1, column=2, sticky="w", padx=(16, 8), pady=4)
        ttk.Entry(settings, textvariable=self.timeout_var, width=10).grid(row=1, column=3, sticky="w", pady=4)
        ttk.Label(settings, text="API key:").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(settings, textvariable=self.api_key_var, width=72, show="*").grid(row=2, column=1, columnspan=3, sticky="ew", pady=4)
        ttk.Label(settings, text="Extra instructions:").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(settings, textvariable=self.extra_var, width=72).grid(row=3, column=1, columnspan=3, sticky="ew", pady=4)
        settings.columnconfigure(1, weight=1)

        image_row = ttk.Frame(outer)
        image_row.pack(fill=tk.X, pady=10)
        ttk.Label(image_row, text="Receipt JPG:").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Entry(image_row, textvariable=self.image_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(image_row, text="Browse", command=self.choose_image).pack(side=tk.LEFT, padx=6)

        fields_frame = ttk.LabelFrame(outer, text="Return fields (10 customizable slots)", padding=10)
        fields_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(fields_frame, text="Field name").grid(row=0, column=0, sticky="w", padx=4)
        ttk.Label(fields_frame, text="What should the VL return?").grid(row=0, column=1, sticky="w", padx=4)

        configured_fields = self.expense_config.get("fields") or DEFAULT_EXPENSE_FIELDS
        normalized_fields = normalize_field_specs(configured_fields)
        while len(normalized_fields) < 10:
            default = DEFAULT_EXPENSE_FIELDS[len(normalized_fields)]
            normalized_fields.append(ExpenseFieldSpec(default["name"], default["description"]))

        self.field_vars.clear()
        for index in range(10):
            spec = normalized_fields[index]
            name_var = tk.StringVar(value=spec.name)
            description_var = tk.StringVar(value=spec.description)
            self.field_vars.append((name_var, description_var))
            ttk.Entry(fields_frame, textvariable=name_var, width=26).grid(row=index + 1, column=0, sticky="ew", padx=4, pady=2)
            ttk.Entry(fields_frame, textvariable=description_var, width=82).grid(row=index + 1, column=1, sticky="ew", padx=4, pady=2)
        fields_frame.columnconfigure(1, weight=1)

        result_frame = ttk.LabelFrame(outer, text="Extraction result", padding=10)
        result_frame.pack(fill=tk.BOTH, expand=True)
        self.result_text = tk.Text(result_frame, height=12, width=110)
        self.result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(10, 0))
        self.analyze_button = ttk.Button(buttons, text="Analyze Receipt", command=self.analyze_receipt)
        self.analyze_button.pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Save Settings", command=self.save_settings).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Export JSON", command=self.export_json).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Export CSV", command=self.export_csv).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Close", command=top.destroy).pack(side=tk.RIGHT, padx=4)

    def choose_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose receipt JPG",
            filetypes=[("JPEG Images", "*.jpg *.jpeg"), ("All Files", "*.*")],
        )
        if path:
            self.image_path_var.set(path)

    def collect_field_specs(self) -> list[ExpenseFieldSpec]:
        specs = [ExpenseFieldSpec(name.get(), desc.get()) for name, desc in self.field_vars]
        return normalize_field_specs(specs)

    def save_settings(self) -> None:
        try:
            timeout = max(1, int(float(self.timeout_var.get() or 60)))
        except ValueError:
            timeout = 60
        self.expense_config.update(
            {
                "endpoint_url": self.endpoint_var.get().strip(),
                "api_key": self.api_key_var.get().strip(),
                "model": self.model_var.get().strip(),
                "timeout_seconds": timeout,
                "extra_instructions": self.extra_var.get().strip(),
                "fields": [asdict(spec) for spec in self.collect_field_specs()],
            }
        )
        if callable(self.on_config_saved):
            self.on_config_saved()
        messagebox.showinfo("Saved", "Expense VL settings saved.")

    def analyze_receipt(self) -> None:
        self.save_settings_without_popup()
        if self.analyze_button:
            self.analyze_button.configure(state="disabled")
        self.write_result("Analyzing receipt...\n")
        threading.Thread(target=self._analyze_worker, daemon=True).start()

    def save_settings_without_popup(self) -> None:
        try:
            timeout = max(1, int(float(self.timeout_var.get() or 60)))
        except ValueError:
            timeout = 60
        self.expense_config.update(
            {
                "endpoint_url": self.endpoint_var.get().strip(),
                "api_key": self.api_key_var.get().strip(),
                "model": self.model_var.get().strip(),
                "timeout_seconds": timeout,
                "extra_instructions": self.extra_var.get().strip(),
                "fields": [asdict(spec) for spec in self.collect_field_specs()],
            }
        )
        if callable(self.on_config_saved):
            self.on_config_saved()

    def _analyze_worker(self) -> None:
        try:
            client = ExpenseVLClient(
                endpoint_url=self.endpoint_var.get(),
                api_key=self.api_key_var.get(),
                model=self.model_var.get(),
                timeout_seconds=int(float(self.timeout_var.get() or 60)),
            )
            result = client.extract_receipt(
                self.image_path_var.get(),
                self.collect_field_specs(),
                extra_instructions=self.extra_var.get(),
            )
            self.last_result = result
            self.parent.after(0, lambda: self.write_result(result.to_json()))
        except Exception as exc:
            self.parent.after(0, lambda: self.write_result(f"Error: {exc}\n"))
        finally:
            if self.analyze_button:
                self.parent.after(0, lambda: self.analyze_button.configure(state="normal"))

    def write_result(self, text: str) -> None:
        if not self.result_text:
            return
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", text)

    def export_json(self) -> None:
        if not self.last_result:
            messagebox.showwarning("No Result", "Analyze a receipt before exporting.")
            return
        path = filedialog.asksaveasfilename(
            title="Save expense extraction JSON",
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as output:
            output.write(self.last_result.to_json())
        messagebox.showinfo("Saved", f"Saved JSON:\n{path}")

    def export_csv(self) -> None:
        if not self.last_result:
            messagebox.showwarning("No Result", "Analyze a receipt before exporting.")
            return
        path = filedialog.asksaveasfilename(
            title="Save expense extraction CSV",
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["field", "value"])
            for key, value in self.last_result.fields.items():
                writer.writerow([key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value])
        messagebox.showinfo("Saved", f"Saved CSV:\n{path}")


def open_expense_receipt_dialog(parent: tk.Misc, config: dict[str, Any] | None = None, on_config_saved=None) -> ExpenseReceiptDialog:
    dialog = ExpenseReceiptDialog(parent, config=config, on_config_saved=on_config_saved)
    dialog.show()
    return dialog


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Expense Receipt VL Extraction")
    runtime_config: dict[str, Any] = {}
    open_expense_receipt_dialog(root, runtime_config)
    root.mainloop()
