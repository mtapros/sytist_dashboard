import json
import os
import tempfile
import unittest

from expense_module import (
    ExpenseFieldSpec,
    ExpenseReceiptDialog,
    ExpenseVLClient,
    MAX_EXPENSE_FIELD_SLOTS,
    format_review_value,
    mousewheel_button_to_scroll_units,
    mousewheel_delta_to_scroll_units,
    normalize_field_specs,
)


class ExpenseModuleTests(unittest.TestCase):
    def test_payload_tells_model_to_preserve_visible_receipt_year(self):
        fd, image_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        try:
            with open(image_path, "wb") as image_file:
                image_file.write(b"fake jpg bytes")
            client = ExpenseVLClient("http://localhost:1234/v1/chat/completions", model="vl-model")

            payload = client.build_payload(
                image_path,
                [ExpenseFieldSpec("receipt_date", "Transaction date in YYYY-MM-DD format if visible.")],
            )
        finally:
            os.unlink(image_path)

        system_prompt = payload["messages"][0]["content"]
        user_prompt = json.loads(payload["messages"][1]["content"][0]["text"])

        self.assertIn("read the year exactly as printed", system_prompt)
        self.assertIn("do not infer", system_prompt)
        self.assertIn("2023 and 2026", " ".join(user_prompt["date_rules"]))

    def test_blank_edited_review_value_is_shown_as_not_found(self):
        self.assertEqual(format_review_value("   "), "Not found")

    def test_mousewheel_delta_converts_to_scroll_units(self):
        self.assertEqual(mousewheel_delta_to_scroll_units(120), -1)
        self.assertEqual(mousewheel_delta_to_scroll_units(-120), 1)
        self.assertEqual(mousewheel_delta_to_scroll_units(240), -2)
        self.assertEqual(mousewheel_delta_to_scroll_units(1), -1)
        self.assertEqual(mousewheel_delta_to_scroll_units(0), 0)

    def test_linux_mousewheel_buttons_convert_to_scroll_units(self):
        self.assertEqual(mousewheel_button_to_scroll_units(4), -1)
        self.assertEqual(mousewheel_button_to_scroll_units(5), 1)
        self.assertEqual(mousewheel_button_to_scroll_units(1), 0)

    def test_expense_window_scrolls_outer_canvas(self):
        class FakeCanvas:
            def __init__(self):
                self.calls = []

            def yview_scroll(self, units, unit_type):
                self.calls.append(("y", units, unit_type))

            def xview_scroll(self, units, unit_type):
                self.calls.append(("x", units, unit_type))

        dialog = object.__new__(ExpenseReceiptDialog)
        dialog.expense_scroll_canvas = FakeCanvas()

        self.assertEqual(dialog.scroll_expense_window(3), "break")
        self.assertEqual(dialog.expense_scroll_canvas.calls, [("y", 3, "units")])

        self.assertEqual(dialog.scroll_expense_window(-2, horizontal=True), "break")
        self.assertEqual(dialog.expense_scroll_canvas.calls[-1], ("x", -2, "units"))

    def test_normalize_field_specs_allows_twenty_optional_slots(self):
        specs = [ExpenseFieldSpec(f"field_{index}", f"desc {index}") for index in range(25)]
        normalized = normalize_field_specs(specs)

        self.assertEqual(len(normalized), MAX_EXPENSE_FIELD_SLOTS)
        self.assertEqual(normalized[-1].name, f"field_{MAX_EXPENSE_FIELD_SLOTS - 1}")


if __name__ == "__main__":
    unittest.main()
