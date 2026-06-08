import json
import os
import tempfile
import unittest

from expense_module import ExpenseFieldSpec, ExpenseVLClient, format_review_value


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


if __name__ == "__main__":
    unittest.main()
