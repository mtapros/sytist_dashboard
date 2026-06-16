"""Tests for ProductTypeManager — user-defined product type mappings."""

import os
import sqlite3
import tempfile
import unittest

from product_type_manager import (
    ACTION_CUSTOM,
    ACTION_PRINT_SIZE,
    ACTION_SKIP,
    ProductTypeManager,
)


def _make_manager():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return ProductTypeManager(db_path=path), path


class ProductTypeManagerInitTests(unittest.TestCase):
    def test_init_creates_table(self):
        mgr, path = _make_manager()
        try:
            with sqlite3.connect(path) as con:
                cur = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='product_type_mappings'"
                )
                self.assertIsNotNone(cur.fetchone())
        finally:
            os.unlink(path)


class ProductTypeManagerMappingTests(unittest.TestCase):
    def setUp(self):
        self.mgr, self.path = _make_manager()

    def tearDown(self):
        os.unlink(self.path)

    def test_set_and_get_print_size(self):
        self.mgr.set_mapping("Custom 5x7 Print", ACTION_PRINT_SIZE, "5x7")
        result = self.mgr.get_mapping("Custom 5x7 Print")
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], ACTION_PRINT_SIZE)
        self.assertEqual(result["value"], "5x7")

    def test_set_and_get_skip(self):
        self.mgr.set_mapping("Digital Download", ACTION_SKIP)
        result = self.mgr.get_mapping("Digital Download")
        self.assertEqual(result["action"], ACTION_SKIP)

    def test_set_and_get_custom(self):
        self.mgr.set_mapping("Mystery Box", ACTION_CUSTOM, "Specials")
        result = self.mgr.get_mapping("Mystery Box")
        self.assertEqual(result["action"], ACTION_CUSTOM)
        self.assertEqual(result["value"], "Specials")

    def test_get_mapping_unknown_returns_none(self):
        self.assertIsNone(self.mgr.get_mapping("Totally Unknown Product"))

    def test_replace_mapping(self):
        self.mgr.set_mapping("My Print", ACTION_SKIP)
        self.mgr.set_mapping("My Print", ACTION_PRINT_SIZE, "4x6")
        result = self.mgr.get_mapping("My Print")
        self.assertEqual(result["action"], ACTION_PRINT_SIZE)
        self.assertEqual(result["value"], "4x6")

    def test_delete_mapping(self):
        self.mgr.set_mapping("Temp", ACTION_SKIP)
        self.assertTrue(self.mgr.is_mapped("Temp"))
        self.mgr.delete_mapping("Temp")
        self.assertFalse(self.mgr.is_mapped("Temp"))

    def test_delete_nonexistent_is_safe(self):
        self.mgr.delete_mapping("Does Not Exist")  # should not raise

    def test_is_mapped_true(self):
        self.mgr.set_mapping("Known", ACTION_SKIP)
        self.assertTrue(self.mgr.is_mapped("Known"))

    def test_is_mapped_false(self):
        self.assertFalse(self.mgr.is_mapped("Unknown"))

    def test_invalid_action_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.set_mapping("X", "invalid_action")

    def test_get_all_mappings(self):
        names = ["Beta Print", "Alpha Print", "Gamma Print"]
        for n in names:
            self.mgr.set_mapping(n, ACTION_SKIP)
        all_rows = self.mgr.get_all_mappings()
        returned_names = [r[0] for r in all_rows]
        self.assertEqual(returned_names, sorted(names))

    def test_get_all_mappings_empty(self):
        self.assertEqual(self.mgr.get_all_mappings(), [])

    def test_shared_db_with_action_log(self):
        """Both stores can share the same SQLite file without conflict."""
        from action_log import ActionLogStore

        log = ActionLogStore(db_path=self.path)
        log.log_action("ORD-1", "printed")
        self.mgr.set_mapping("TestProd", ACTION_SKIP)
        # Both stores should still work independently
        self.assertEqual(len(log.get_actions_for_order("ORD-1")), 1)
        self.assertIsNotNone(self.mgr.get_mapping("TestProd"))


if __name__ == "__main__":
    unittest.main()
