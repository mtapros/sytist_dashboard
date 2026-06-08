"""Receipt expense extraction module.

This module provides a Tkinter UI for loading a JPG/JPEG store receipt,
sending it to an LM Studio vision-language model, and reviewing the returned
expense fields for approval.

The default endpoint targets LM Studio on the local network at
``192.168.34.82:1234`` using LM Studio's OpenAI-compatible API. The dialog can
fetch available LM Studio models, display the selected receipt image, show the
extracted data in a clean review table, and mark the extraction approved or in
need of correction.
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

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:  # pragma: no cover - depends on optional runtime package
    Image = None
    ImageTk = None
    HAS_PIL = False


LM_STUDIO_HOST = "192.168.34.82"
LM_STUDIO_PORT = 1234
DEFAULT_LM_STUDIO_ENDPOINT = f"http://{LM_STUDIO_HOST}:{LM_STUDIO_PORT}/v1/chat/completions"

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

IMPORTANT_FIELD_ORDER = [
    "merchant_name",
    "receipt_date",
    "receipt_time",
    "subtotal",
    "tax",
    "tip",
    "total",
    "payment_method",
    "expense_category",
    "items",
    "confidence",
    "notes",
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
    approval_status: str = "Pending Review"
    approved_at: str = ""

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

    def list_models(self) -> list[str]:
        """Return model IDs from an OpenAI-compatible /v1/models endpoint."""

        models_url = build_models_url(self.endpoint_url)
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(models_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise ExpenseVLClientError(f"Model list returned HTTP {error.code}: {body or error.reason}") from error
        except urllib.error.URLError as error:
            raise ExpenseVLClientError(f"Could not reach LM Studio models endpoint: {error.reason}") from error
        except TimeoutError as error:
            raise ExpenseVLClientError("LM Studio model list timed out.") from error

        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as error:
            raise ExpenseVLClientError(f"Models endpoint did not return JSON: {raw_body[:500]}") from error

        data = parsed.get("data", []) if isinstance(parsed, dict) else []
        models: list[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    model_id = str(item.get("id", "")).strip()
                    if model_id:
                        models.append(model_id)
                elif isinstance(item, str) and item.strip():
                    models.append(item.strip())
        return sorted(dict.fromkeys(models))

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
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise ExpenseVLClientError(f"VL endpoint returned HTTP {error.code}: {body or error.reason}") from error
        except urllib.error.URLError as error:
            raise ExpenseVLClientError(f"Could not reach VL endpoint: {error.reason}") from error
        except TimeoutError as error:
            raise ExpenseVLClientError("VL endpoint timed out.") from error
        except Exception as error:  # pragma: no cover - defensive wrapper
            raise ExpenseVLClientError(f"Unexpected VL endpoint error: {error}") from error

        try:
            raw_response = json.loads(raw_body)
        except json.JSONDecodeError as error:
            raise ExpenseVLClientError(f"VL endpoint did not return JSON: {raw_body[:500]}") from error

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
            "Use null when a requested value is not visible. Preserve currency values as strings exactly as read when possible. "
            "For receipt dates, read the year exactly as printed; do not infer, autocorrect, or substitute a different year."
        )
        user_prompt = {
            "task": "Extract the requested receipt fields from this image.",
            "requested_fields": requested_schema,
            "date_rules": [
                "If a receipt date includes a year, preserve that year exactly as visible.",
                "Pay close attention to similar-looking years such as 2023 and 2026.",
                "If the date or year is ambiguous, return the best visible value and explain the uncertainty in notes.",
            ],
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
            # LM Studio currently accepts json_schema or text here. Use text and
            # rely on the prompt/parser for strict JSON extraction.
            "response_format": {"type": "text"},
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


def build_models_url(endpoint_url: str) -> str:
    """Derive LM Studio/OpenAI-compatible /v1/models URL from chat endpoint."""

    endpoint = (endpoint_url or DEFAULT_LM_STUDIO_ENDPOINT).strip().rstrip("/")
    if endpoint.endswith("/v1/chat/completions"):
        return endpoint[: -len("/chat/completions")] + "/models"
    if endpoint.endswith("/chat/completions"):
        return endpoint[: -len("/chat/completions")] + "/models"
    if endpoint.endswith("/v1"):
        return endpoint + "/models"
    return endpoint + "/v1/models"


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
        normalized_key = normalize_field_name(key)
        if normalized_key in lower_lookup and normalized_key not in coerced:
            coerced[normalized_key] = lower_lookup[normalized_key]
    return coerced


def default_expense_config() -> dict[str, Any]:
    return {
        "endpoint_url": DEFAULT_LM_STUDIO_ENDPOINT,
        "api_key": "",
        "model": "",
        "timeout_seconds": 60,
        "fields": DEFAULT_EXPENSE_FIELDS,
        "extra_instructions": "",
    }


def normalize_expense_config(expense_config: dict[str, Any]) -> dict[str, Any]:
    defaults = default_expense_config()
    for key, value in defaults.items():
        expense_config.setdefault(key, value)
    if not str(expense_config.get("endpoint_url", "")).strip():
        expense_config["endpoint_url"] = DEFAULT_LM_STUDIO_ENDPOINT
    return expense_config


def field_label(field_name: str) -> str:
    return str(field_name or "").replace("_", " ").strip().title()


def format_review_value(value: Any) -> str:
    if value is None:
        return "Not found"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        if not value:
            return "None"
        lines = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                parts = [f"{field_label(k)}: {format_review_value(v)}" for k, v in item.items()]
                lines.append(f"{index}. " + " | ".join(parts))
            else:
                lines.append(f"{index}. {format_review_value(item)}")
        return "\n".join(lines)
    if isinstance(value, dict):
        if not value:
            return "None"
        return "\n".join(f"{field_label(k)}: {format_review_value(v)}" for k, v in value.items())
    text = str(value).strip()
    return text if text else "Not found"


def ordered_review_fields(fields: dict[str, Any]) -> list[tuple[str, Any]]:
    remaining = dict(fields or {})
    ordered: list[tuple[str, Any]] = []
    for key in IMPORTANT_FIELD_ORDER:
        if key in remaining:
            ordered.append((key, remaining.pop(key)))
    for key in sorted(remaining):
        ordered.append((key, remaining[key]))
    return ordered


class ExpenseReceiptDialog:
    """Tkinter dialog for receipt image upload and configurable extraction."""

    def __init__(self, parent: tk.Misc, config: dict[str, Any] | None = None, on_config_saved=None) -> None:
        self.parent = parent
        self.config = config if isinstance(config, dict) else {}
        self.expense_config = normalize_expense_config(self.config.setdefault("expense_vl", default_expense_config()))
        self.on_config_saved = on_config_saved
        self.image_path_var = tk.StringVar(value="")
        self.endpoint_var = tk.StringVar(value=str(self.expense_config.get("endpoint_url", DEFAULT_LM_STUDIO_ENDPOINT)))
        self.api_key_var = tk.StringVar(value=str(self.expense_config.get("api_key", "")))
        self.model_var = tk.StringVar(value=str(self.expense_config.get("model", "")))
        self.timeout_var = tk.StringVar(value=str(self.expense_config.get("timeout_seconds", 60)))
        self.extra_var = tk.StringVar(value=str(self.expense_config.get("extra_instructions", "")))
        self.approval_status_var = tk.StringVar(value="No receipt analyzed yet.")
        self.field_vars: list[tuple[tk.StringVar, tk.StringVar]] = []
        self.last_result: ExpenseExtractionResult | None = None
        self.result_text: tk.Text | None = None
        self.approval_tree: ttk.Treeview | None = None
        self.approval_item_fields: dict[str, str] = {}
        self.review_edit_widget: ttk.Entry | None = None
        self.analyze_button: ttk.Button | None = None
        self.approve_button: ttk.Button | None = None
        self.needs_correction_button: ttk.Button | None = None
        self.model_combo: ttk.Combobox | None = None
        self.model_button: ttk.Button | None = None
        self.receipt_canvas: tk.Canvas | None = None
        self.receipt_scroll_y: ttk.Scrollbar | None = None
        self.receipt_scroll_x: ttk.Scrollbar | None = None
        self.receipt_photo = None
        self.receipt_preview_image = None

    def show(self) -> None:
        top = tk.Toplevel(self.parent)
        top.title("Expense Receipt VL Extraction")
        top.geometry("1240x880")
        top.transient(self.parent)

        outer = ttk.Frame(top, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            outer,
            text=(
                "Load a JPG receipt, choose an LM Studio VL model from "
                f"{LM_STUDIO_HOST}:{LM_STUDIO_PORT}, then review and approve the extracted expense data."
            ),
            wraplength=1180,
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(0, 10))

        settings = ttk.LabelFrame(outer, text="LM Studio VL settings", padding=10)
        settings.pack(fill=tk.X)
        ttk.Label(settings, text="Endpoint URL:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(settings, textvariable=self.endpoint_var, width=72).grid(row=0, column=1, columnspan=4, sticky="ew", pady=4)
        ttk.Button(settings, text="Use LM Studio Default", command=self.use_lm_studio_default).grid(row=0, column=5, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(settings, text="Model:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self.model_combo = ttk.Combobox(settings, textvariable=self.model_var, width=42)
        self.model_combo.grid(row=1, column=1, sticky="ew", pady=4)
        self.model_button = ttk.Button(settings, text="Pick Available Model", command=self.pick_model)
        self.model_button.grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=4)
        ttk.Label(settings, text="Timeout:").grid(row=1, column=3, sticky="w", padx=(16, 8), pady=4)
        ttk.Entry(settings, textvariable=self.timeout_var, width=10).grid(row=1, column=4, sticky="w", pady=4)

        ttk.Label(settings, text="API key:").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(settings, textvariable=self.api_key_var, width=72, show="*").grid(row=2, column=1, columnspan=4, sticky="ew", pady=4)
        ttk.Label(settings, text="Extra instructions:").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(settings, textvariable=self.extra_var, width=72).grid(row=3, column=1, columnspan=4, sticky="ew", pady=4)
        settings.columnconfigure(1, weight=1)

        image_row = ttk.Frame(outer)
        image_row.pack(fill=tk.X, pady=10)
        ttk.Label(image_row, text="Receipt JPG:").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Entry(image_row, textvariable=self.image_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(image_row, text="Browse", command=self.choose_image).pack(side=tk.LEFT, padx=6)

        body = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        left_side = ttk.Frame(body)
        body.add(left_side, weight=2)
        right_side = ttk.Frame(body)
        body.add(right_side, weight=1)

        fields_frame = ttk.LabelFrame(left_side, text="Return fields (10 customizable slots)", padding=10)
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

        review_frame = ttk.LabelFrame(left_side, text="Approval Review", padding=10)
        review_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            review_frame,
            text="Review the extracted fields below. Double-click an extracted value to edit it before approving.",
            foreground="#555555",
        ).pack(anchor="w", pady=(0, 6))
        ttk.Label(review_frame, textvariable=self.approval_status_var, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))

        approval_table = ttk.Frame(review_frame)
        approval_table.pack(fill=tk.BOTH, expand=True)
        self.approval_tree = ttk.Treeview(approval_table, columns=("Field", "Value"), show="headings", height=11)
        self.approval_tree.heading("Field", text="Field")
        self.approval_tree.heading("Value", text="Extracted Value")
        self.approval_tree.column("Field", width=180, anchor=tk.W)
        self.approval_tree.column("Value", width=640, anchor=tk.W)
        self.approval_tree.grid(row=0, column=0, sticky="nsew")
        review_scroll_y = ttk.Scrollbar(approval_table, orient=tk.VERTICAL, command=self.approval_tree.yview)
        review_scroll_x = ttk.Scrollbar(approval_table, orient=tk.HORIZONTAL, command=self.approval_tree.xview)
        self.approval_tree.configure(yscrollcommand=review_scroll_y.set, xscrollcommand=review_scroll_x.set)
        review_scroll_y.grid(row=0, column=1, sticky="ns")
        review_scroll_x.grid(row=1, column=0, sticky="ew")
        approval_table.rowconfigure(0, weight=1)
        approval_table.columnconfigure(0, weight=1)
        self.approval_tree.bind("<Double-Button-1>", self.begin_approval_value_edit)

        raw_frame = ttk.LabelFrame(left_side, text="Raw JSON / diagnostics", padding=8)
        raw_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.result_text = tk.Text(raw_frame, height=7, width=90)
        self.result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        raw_scroll = ttk.Scrollbar(raw_frame, orient=tk.VERTICAL, command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=raw_scroll.set)
        raw_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        receipt_frame = ttk.LabelFrame(right_side, text="Receipt preview", padding=8)
        receipt_frame.pack(fill=tk.BOTH, expand=True)
        self.receipt_canvas = tk.Canvas(receipt_frame, background="#f7f7f7", highlightthickness=0)
        self.receipt_scroll_y = ttk.Scrollbar(receipt_frame, orient=tk.VERTICAL, command=self.receipt_canvas.yview)
        self.receipt_scroll_x = ttk.Scrollbar(receipt_frame, orient=tk.HORIZONTAL, command=self.receipt_canvas.xview)
        self.receipt_canvas.configure(yscrollcommand=self.receipt_scroll_y.set, xscrollcommand=self.receipt_scroll_x.set)
        self.receipt_canvas.grid(row=0, column=0, sticky="nsew")
        self.receipt_scroll_y.grid(row=0, column=1, sticky="ns")
        self.receipt_scroll_x.grid(row=1, column=0, sticky="ew")
        receipt_frame.rowconfigure(0, weight=1)
        receipt_frame.columnconfigure(0, weight=1)
        self.receipt_canvas.create_text(180, 180, text="Choose a receipt JPG\nto display it here.", fill="#666666", justify=tk.CENTER)

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(0, 0))
        self.analyze_button = ttk.Button(buttons, text="Analyze Receipt", command=self.analyze_receipt)
        self.analyze_button.pack(side=tk.LEFT, padx=4)
        self.approve_button = ttk.Button(buttons, text="Approve", command=self.approve_result, state="disabled")
        self.approve_button.pack(side=tk.LEFT, padx=4)
        self.needs_correction_button = ttk.Button(buttons, text="Needs Correction", command=self.mark_needs_correction, state="disabled")
        self.needs_correction_button.pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Save Settings", command=self.save_settings).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Export JSON", command=self.export_json).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Export CSV", command=self.export_csv).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Close", command=top.destroy).pack(side=tk.RIGHT, padx=4)

    def use_lm_studio_default(self) -> None:
        self.endpoint_var.set(DEFAULT_LM_STUDIO_ENDPOINT)

    def choose_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose receipt JPG",
            filetypes=[("JPEG Images", "*.jpg *.jpeg"), ("All Files", "*.*")],
        )
        if path:
            self.image_path_var.set(path)
            self.display_receipt(path)
            self.approval_status_var.set("Receipt loaded. Analyze it to extract expense data.")

    def display_receipt(self, path: str) -> None:
        if not self.receipt_canvas:
            return
        self.receipt_canvas.delete("all")
        if not HAS_PIL or Image is None or ImageTk is None:
            self.receipt_canvas.create_text(
                180,
                180,
                text="Install Pillow to display receipt previews:\npip install Pillow",
                fill="#a33",
                justify=tk.CENTER,
            )
            return
        try:
            image = Image.open(path).convert("RGB")
            image.thumbnail((460, 980), Image.Resampling.LANCZOS)
            self.receipt_preview_image = image
            self.receipt_photo = ImageTk.PhotoImage(image)
            self.receipt_canvas.create_image(0, 0, anchor=tk.NW, image=self.receipt_photo)
            self.receipt_canvas.configure(scrollregion=(0, 0, image.width, image.height))
        except Exception as error:
            self.receipt_canvas.create_text(
                180,
                180,
                text=f"Could not display receipt:\n{error}",
                fill="#a33",
                justify=tk.CENTER,
            )

    def collect_field_specs(self) -> list[ExpenseFieldSpec]:
        specs = [ExpenseFieldSpec(name.get(), desc.get()) for name, desc in self.field_vars]
        return normalize_field_specs(specs)

    def _client_from_settings(self, timeout_override: int | None = None) -> ExpenseVLClient:
        try:
            timeout = max(1, int(float(self.timeout_var.get() or 60)))
        except ValueError:
            timeout = 60
        if timeout_override is not None:
            timeout = timeout_override
        return ExpenseVLClient(
            endpoint_url=self.endpoint_var.get(),
            api_key=self.api_key_var.get(),
            model=self.model_var.get(),
            timeout_seconds=timeout,
        )

    def pick_model(self) -> None:
        if self.model_button:
            self.model_button.configure(state="disabled")
        self.write_result(f"Fetching models from {build_models_url(self.endpoint_var.get())}...\n")
        threading.Thread(target=self._pick_model_worker, daemon=True).start()

    def _pick_model_worker(self) -> None:
        try:
            models = self._client_from_settings(timeout_override=10).list_models()
            self.parent.after(0, lambda found=models: self.show_model_picker(found))
        except Exception as error:
            error_message = str(error)
            self.parent.after(0, lambda msg=error_message: messagebox.showerror("Model List Error", msg))
            self.parent.after(0, lambda msg=error_message: self.write_result(f"Error fetching models: {msg}\n"))
        finally:
            if self.model_button:
                self.parent.after(0, lambda: self.model_button.configure(state="normal"))

    def show_model_picker(self, models: list[str]) -> None:
        if self.model_combo:
            self.model_combo.configure(values=models)
        if not models:
            messagebox.showwarning(
                "No Models Found",
                "LM Studio responded, but no models were returned. Make sure a vision-capable model is loaded/available.",
            )
            return

        picker = tk.Toplevel(self.parent)
        picker.title("Pick LM Studio Model")
        picker.geometry("520x360")
        picker.transient(self.parent)
        picker.grab_set()

        frame = ttk.Frame(picker, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Available LM Studio models:").pack(anchor="w", pady=(0, 8))
        listbox = tk.Listbox(frame, height=12)
        listbox.pack(fill=tk.BOTH, expand=True)
        for model in models:
            listbox.insert(tk.END, model)
            if model == self.model_var.get():
                listbox.selection_set(tk.END)
        if not listbox.curselection():
            listbox.selection_set(0)

        def select_model() -> None:
            selection = listbox.curselection()
            if selection:
                self.model_var.set(models[selection[0]])
                self.save_settings_without_popup()
            picker.destroy()

        button_row = ttk.Frame(frame)
        button_row.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(button_row, text="Use Selected Model", command=select_model).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_row, text="Cancel", command=picker.destroy).pack(side=tk.RIGHT, padx=4)
        listbox.bind("<Double-Button-1>", lambda _event: select_model())

    def save_settings(self) -> None:
        self.save_settings_without_popup()
        messagebox.showinfo("Saved", "Expense VL settings saved.")

    def analyze_receipt(self) -> None:
        self.save_settings_without_popup()
        if self.analyze_button:
            self.analyze_button.configure(state="disabled")
        if self.approve_button:
            self.approve_button.configure(state="disabled")
        if self.needs_correction_button:
            self.needs_correction_button.configure(state="disabled")
        self.approval_status_var.set("Analyzing receipt with LM Studio...")
        self.clear_approval_view()
        self.write_result("Analyzing receipt...\n")
        threading.Thread(target=self._analyze_worker, daemon=True).start()

    def save_settings_without_popup(self) -> None:
        try:
            timeout = max(1, int(float(self.timeout_var.get() or 60)))
        except ValueError:
            timeout = 60
        self.expense_config.update(
            {
                "endpoint_url": self.endpoint_var.get().strip() or DEFAULT_LM_STUDIO_ENDPOINT,
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
            client = self._client_from_settings()
            result = client.extract_receipt(
                self.image_path_var.get(),
                self.collect_field_specs(),
                extra_instructions=self.extra_var.get(),
            )
            self.last_result = result
            self.parent.after(0, lambda res=result: self.show_extraction_result(res))
        except Exception as error:
            error_message = str(error)
            self.parent.after(0, lambda msg=error_message: self.show_analysis_error(msg))
        finally:
            if self.analyze_button:
                self.parent.after(0, lambda: self.analyze_button.configure(state="normal"))

    def clear_approval_view(self) -> None:
        self.cancel_review_edit()
        self.approval_item_fields.clear()
        if self.approval_tree:
            self.approval_tree.delete(*self.approval_tree.get_children())

    def show_extraction_result(self, result: ExpenseExtractionResult) -> None:
        self.write_result(result.to_json())
        self.populate_approval_view(result)
        result.approval_status = "Pending Review"
        result.approved_at = ""
        self.approval_status_var.set(
            f"Pending Review — extracted {len(result.fields)} field(s) at {result.extracted_at}. Verify before approving."
        )
        if self.approve_button:
            self.approve_button.configure(state="normal")
        if self.needs_correction_button:
            self.needs_correction_button.configure(state="normal")

    def show_analysis_error(self, message: str) -> None:
        self.approval_status_var.set("Analysis failed. Check the raw diagnostics below.")
        self.clear_approval_view()
        self.write_result(f"Error: {message}\n")

    def populate_approval_view(self, result: ExpenseExtractionResult) -> None:
        if not self.approval_tree:
            return
        self.clear_approval_view()
        for key, value in ordered_review_fields(result.fields):
            display_value = format_review_value(value)
            tag = "missing" if display_value == "Not found" else "ok"
            item_id = self.approval_tree.insert("", tk.END, values=(field_label(key), display_value), tags=(tag,))
            self.approval_item_fields[item_id] = key
        self.approval_tree.tag_configure("missing", foreground="#9a3412")
        self.approval_tree.tag_configure("ok", foreground="#111827")

    def begin_approval_value_edit(self, event: tk.Event) -> None:
        if not self.approval_tree or not self.last_result:
            return
        item_id = self.approval_tree.identify_row(event.y)
        column = self.approval_tree.identify_column(event.x)
        if not item_id or column != "#2" or item_id not in self.approval_item_fields:
            return
        self.cancel_review_edit()
        bbox = self.approval_tree.bbox(item_id, column)
        if not bbox:
            return
        x, y, width, height = bbox
        current_value = self.approval_tree.set(item_id, "Value")
        editor = ttk.Entry(self.approval_tree)
        editor.insert(0, current_value)
        editor.select_range(0, tk.END)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()

        def commit(_event=None) -> None:
            if self.review_edit_widget is not editor:
                return
            self.commit_approval_value_edit(item_id, editor.get())

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", lambda _event: self.cancel_review_edit())
        self.review_edit_widget = editor

    def commit_approval_value_edit(self, item_id: str, edited_value: str) -> None:
        if not self.approval_tree or not self.last_result:
            self.cancel_review_edit()
            return
        field_key = self.approval_item_fields.get(item_id)
        if not field_key:
            self.cancel_review_edit()
            return
        self.last_result.fields[field_key] = edited_value.strip()
        display_value = format_review_value(self.last_result.fields[field_key])
        tag = "missing" if display_value == "Not found" else "ok"
        self.approval_tree.item(item_id, values=(field_label(field_key), display_value), tags=(tag,))
        self.cancel_review_edit()
        self.approval_status_var.set("Pending Review — edited extracted values. Verify before approving.")
        self.write_result(self.last_result.to_json())

    def cancel_review_edit(self) -> None:
        if self.review_edit_widget:
            self.review_edit_widget.destroy()
            self.review_edit_widget = None

    def approve_result(self) -> None:
        if not self.last_result:
            messagebox.showwarning("No Result", "Analyze a receipt before approving.")
            return
        approved_at = datetime.now().isoformat(timespec="seconds")
        self.last_result.approval_status = "Approved"
        self.last_result.approved_at = approved_at
        self.approval_status_var.set(f"Approved at {approved_at}. Export JSON or CSV when ready.")
        self.write_result(self.last_result.to_json())
        messagebox.showinfo("Approved", "Expense extraction approved.")

    def mark_needs_correction(self) -> None:
        if not self.last_result:
            messagebox.showwarning("No Result", "Analyze a receipt before marking it for correction.")
            return
        self.last_result.approval_status = "Needs Correction"
        self.last_result.approved_at = ""
        self.approval_status_var.set("Needs Correction — adjust requested fields/instructions or re-analyze the receipt.")
        self.write_result(self.last_result.to_json())

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
            writer.writerow(["approval_status", self.last_result.approval_status])
            writer.writerow(["approved_at", self.last_result.approved_at])
            writer.writerow(["extracted_at", self.last_result.extracted_at])
            writer.writerow(["model", self.last_result.model])
            for key, value in ordered_review_fields(self.last_result.fields):
                writer.writerow([key, format_review_value(value)])
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
