"""Tests for the Orders Window feature added to SytistDashboard."""
import ast
import os
import unittest


SYTIST_PATH = os.path.join(os.path.dirname(__file__), "..", "sytist.py")


def _parse_sytist():
    with open(SYTIST_PATH) as fh:
        return ast.parse(fh.read())


def _read_sytist():
    with open(SYTIST_PATH) as fh:
        return fh.read()


def _dashboard_methods():
    tree = _parse_sytist()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SytistDashboard":
            return {n.name for n in ast.walk(node) if isinstance(n, ast.FunctionDef)}
    return set()


def _get_method_src(method_name: str) -> str | None:
    source = _read_sytist()
    tree = _parse_sytist()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SytistDashboard":
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    lines = source.splitlines()
                    return "\n".join(lines[child.lineno - 1: child.end_lineno])
    return None


class OrdersWindowMethodTests(unittest.TestCase):
    """Verify the Orders Window method exists and has the expected structure."""

    def test_open_orders_window_method_exists(self):
        self.assertIn(
            "open_orders_window",
            _dashboard_methods(),
            "SytistDashboard must have an open_orders_window method",
        )

    def test_orders_window_creates_toplevel(self):
        """open_orders_window should create a tk.Toplevel."""
        method_body = None
        tree = _parse_sytist()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "SytistDashboard":
                for child in ast.walk(node):
                    if isinstance(child, ast.FunctionDef) and child.name == "open_orders_window":
                        method_body = child
                        break
                break

        self.assertIsNotNone(method_body, "open_orders_window not found in SytistDashboard")

        calls = [n for n in ast.walk(method_body) if isinstance(n, ast.Call)]
        call_strs = set()
        for call in calls:
            if isinstance(call.func, ast.Attribute):
                call_strs.add(call.func.attr)
            elif isinstance(call.func, ast.Name):
                call_strs.add(call.func.id)

        self.assertIn("Toplevel", call_strs, "open_orders_window should create a Toplevel window")

    def test_orders_window_has_date_column(self):
        """The Orders Window treeview must include a 'Date' column."""
        method_src = _get_method_src("open_orders_window")
        self.assertIsNotNone(method_src, "open_orders_window not found")
        self.assertIn(
            '"Date"',
            method_src,
            "open_orders_window treeview must include a 'Date' column",
        )

    def test_orders_window_has_split_pane(self):
        """open_orders_window should use a PanedWindow for the split-pane layout."""
        method_src = _get_method_src("open_orders_window")
        self.assertIsNotNone(method_src, "open_orders_window not found")
        self.assertIn("PanedWindow", method_src, "open_orders_window should use a PanedWindow split pane")

    def test_orders_window_has_items_tree(self):
        """open_orders_window should create a second Treeview for order items."""
        method_src = _get_method_src("open_orders_window")
        self.assertIsNotNone(method_src, "open_orders_window not found")
        # Check that there is more than one Treeview call (orders + items)
        self.assertGreaterEqual(
            method_src.count("Treeview"),
            2,
            "open_orders_window should have at least two Treeview widgets (orders + items)",
        )

    def test_orders_window_uses_unicode_checkbox(self):
        """open_orders_window should use Unicode checkbox symbols instead of [ ]."""
        source = _read_sytist()
        # ☐ unchecked and ☑ checked symbols must be present
        self.assertIn("☐", source, "Source should use ☐ Unicode unchecked checkbox symbol")
        self.assertIn("☑", source, "Source should use ☑ Unicode checked checkbox symbol")
        # Old-style text pseudo-checkboxes must not appear in column values
        self.assertNotIn('"[ ]"', source, '[ ] text pseudo-checkbox must not be used')
        self.assertNotIn('"[X]"', source, '[X] text pseudo-checkbox must not be used')

    def test_control_frame_has_open_orders_window_button(self):
        """setup_ui should have a button that calls open_orders_window."""
        source = _read_sytist()
        self.assertIn(
            "open_orders_window",
            source,
            "open_orders_window should be referenced in the source",
        )
        self.assertIn(
            '"Open Orders Window"',
            source,
            "An 'Open Orders Window' button text should be present in sytist.py",
        )

    def test_image_preview_window_method_exists(self):
        """SytistDashboard must have an open_image_preview_window method."""
        self.assertIn(
            "open_image_preview_window",
            _dashboard_methods(),
            "SytistDashboard must have an open_image_preview_window method",
        )

    def test_product_type_manager_method_exists(self):
        """SytistDashboard must have an open_product_type_manager method."""
        self.assertIn(
            "open_product_type_manager",
            _dashboard_methods(),
            "SytistDashboard must have an open_product_type_manager method",
        )

    def test_action_log_method_exists(self):
        """SytistDashboard must have an _open_order_action_log method."""
        self.assertIn(
            "_open_order_action_log",
            _dashboard_methods(),
            "SytistDashboard must have an _open_order_action_log method",
        )


if __name__ == "__main__":
    unittest.main()
