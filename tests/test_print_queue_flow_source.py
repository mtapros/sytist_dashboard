"""Source-level regression tests for queue-first print and queue editing flows."""

from __future__ import annotations

import ast
import os
import unittest


SYTIST_PATH = os.path.join(os.path.dirname(__file__), "..", "sytist.py")


def _read_sytist() -> str:
    with open(SYTIST_PATH, encoding="utf-8") as fh:
        return fh.read()


def _parse_sytist() -> ast.Module:
    return ast.parse(_read_sytist())


def _get_method_src(method_name: str) -> str | None:
    source = _read_sytist()
    lines = source.splitlines()
    tree = _parse_sytist()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SytistDashboard":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    return "\n".join(lines[child.lineno - 1: child.end_lineno])
    return None


class PrintQueueFlowSourceTests(unittest.TestCase):
    def test_order_prints_are_queue_first(self):
        method_src = _get_method_src("print_selected_orders")
        self.assertIsNotNone(method_src)
        self.assertIn("_enqueue_jobs", method_src)
        self.assertNotIn('self.start_print_workflow(jobs, "Order Print")', method_src)
        self.assertIn('"Queued"', method_src)

    def test_file_prints_are_queue_first(self):
        method_src = _get_method_src("print_image_files")
        self.assertIsNotNone(method_src)
        self.assertIn("_enqueue_jobs", method_src)
        self.assertNotIn('self.start_print_workflow(jobs, "Image File Print")', method_src)
        self.assertIn('"Queued"', method_src)

    def test_button_editor_prints_no_longer_direct_print(self):
        source = _read_sytist()
        self.assertNotIn('self.start_print_workflow([job], "Button Print")', source)

    def test_queue_editor_supports_double_click_and_last_printed_at(self):
        method_src = _get_method_src("open_print_queue")
        self.assertIsNotNone(method_src)
        self.assertIn("Last Printed At", method_src)
        self.assertIn('tree.bind("<Double-1>"', method_src)
        self.assertIn("open_queue_item_editor", method_src)
        self.assertIn("open_queue_crop_editor", method_src)
        self.assertIn("_open_button_editor_for_queue_item", method_src)

    def test_crop_editor_uses_prepared_images_instead_of_url_blocker(self):
        method_src = _get_method_src("open_photo_crop_editor")
        self.assertIsNotNone(method_src)
        self.assertIn("_prepare_queue_item_image", method_src)
        self.assertNotIn("requires downloading the image first", method_src)

    def test_queue_print_job_builder_treats_button_size_as_button_workflow(self):
        method_src = _get_method_src("_queue_item_to_print_job")
        self.assertIsNotNone(method_src)
        self.assertIn('qi.source_type == "button" or qi.size_key == "button"', method_src)
        self.assertIn("render_button_sheet", method_src)

    def test_queue_button_editor_checks_for_duplicate_designs(self):
        method_src = _get_method_src("_open_button_editor_for_queue_item")
        self.assertIsNotNone(method_src)
        self.assertIn("_find_prior_button_design_match", method_src)
        self.assertIn("askyesnocancel", method_src)
        self.assertIn("_apply_button_specs_to_queue_item", method_src)

    def test_open_button_editor_from_specs_updates_existing_queue_item(self):
        method_src = _get_method_src("open_button_print_editor_from_specs")
        self.assertIsNotNone(method_src)
        self.assertIn("queue_item_id", method_src)
        self.assertIn("_apply_button_specs_to_queue_item", method_src)
        self.assertIn('"Apply to Queue"', method_src)


if __name__ == "__main__":
    unittest.main()
