"""Tests for the Orders Window feature added to SytistDashboard."""
import ast
import os
import unittest


SYTIST_PATH = os.path.join(os.path.dirname(__file__), "..", "sytist.py")


def _parse_sytist():
    with open(SYTIST_PATH) as fh:
        return ast.parse(fh.read())


def _dashboard_methods():
    tree = _parse_sytist()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SytistDashboard":
            return {n.name for n in ast.walk(node) if isinstance(n, ast.FunctionDef)}
    return set()


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
        tree = _parse_sytist()
        method_body = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "SytistDashboard":
                for child in ast.walk(node):
                    if isinstance(child, ast.FunctionDef) and child.name == "open_orders_window":
                        method_body = child
                        break
                break

        self.assertIsNotNone(method_body, "open_orders_window not found in SytistDashboard")

        # Collect all Call nodes in the method body
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
        with open(SYTIST_PATH) as fh:
            source = fh.read()

        tree = _parse_sytist()
        method_src_lines = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "SytistDashboard":
                for child in ast.walk(node):
                    if isinstance(child, ast.FunctionDef) and child.name == "open_orders_window":
                        lines = source.splitlines()
                        method_src = "\n".join(
                            lines[child.lineno - 1 : child.end_lineno]
                        )
                        method_src_lines.append(method_src)
                        break
                break

        self.assertTrue(method_src_lines, "open_orders_window not found")
        self.assertIn(
            '"Date"',
            method_src_lines[0],
            "open_orders_window treeview must include a 'Date' column",
        )

    def test_control_frame_has_orders_button(self):
        """setup_ui should pack an 'Orders' button that calls open_orders_window."""
        with open(SYTIST_PATH) as fh:
            source = fh.read()

        self.assertIn(
            "open_orders_window",
            source,
            "open_orders_window should be referenced in the source",
        )
        # The button text 'Orders' should appear in setup_ui context
        self.assertIn(
            '"Orders"',
            source,
            "An 'Orders' button text should be present in sytist.py",
        )


if __name__ == "__main__":
    unittest.main()
