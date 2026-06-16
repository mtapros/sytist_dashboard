"""Tests for ActionLogStore — per-order action logging with SQLite."""

import os
import sqlite3
import tempfile
import unittest

from action_log import ActionLogStore


def _make_store():
    """Return an ActionLogStore backed by a temporary file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return ActionLogStore(db_path=path), path


class ActionLogStoreInitTests(unittest.TestCase):
    def test_init_creates_table(self):
        store, path = _make_store()
        try:
            with sqlite3.connect(path) as con:
                cur = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='order_actions'"
                )
                self.assertIsNotNone(cur.fetchone())
        finally:
            os.unlink(path)

    def test_second_init_is_idempotent(self):
        store, path = _make_store()
        try:
            ActionLogStore(db_path=path)  # re-init should not raise
        finally:
            os.unlink(path)


class ActionLogStoreWriteTests(unittest.TestCase):
    def setUp(self):
        self.store, self.path = _make_store()

    def tearDown(self):
        os.unlink(self.path)

    def test_log_action_basic(self):
        self.store.log_action("ORD-001", "printed", "5x7 × 2")
        rows = self.store.get_actions_for_order("ORD-001")
        self.assertEqual(len(rows), 1)
        ts, action_type, details = rows[0]
        self.assertEqual(action_type, "printed")
        self.assertEqual(details, "5x7 × 2")
        self.assertIn("T", ts)  # ISO timestamp

    def test_log_action_no_details(self):
        self.store.log_action("ORD-002", "viewed")
        rows = self.store.get_actions_for_order("ORD-002")
        self.assertEqual(rows[0][2], "")

    def test_log_action_multiple_entries(self):
        for action in ["viewed", "printed", "shipped"]:
            self.store.log_action("ORD-003", action)
        rows = self.store.get_actions_for_order("ORD-003")
        self.assertEqual(len(rows), 3)
        self.assertEqual([r[1] for r in rows], ["viewed", "printed", "shipped"])

    def test_log_actions_bulk(self):
        entries = [
            ("ORD-010", "printed", "4x6"),
            ("ORD-011", "shipped", ""),
            ("ORD-012", "zoho_push", "INV-007"),
        ]
        self.store.log_actions_bulk(entries)
        for oid, at, _ in entries:
            rows = self.store.get_actions_for_order(oid)
            self.assertEqual(rows[0][1], at)

    def test_log_actions_bulk_empty_is_safe(self):
        self.store.log_actions_bulk([])  # should not raise


class ActionLogStoreReadTests(unittest.TestCase):
    def setUp(self):
        self.store, self.path = _make_store()

    def tearDown(self):
        os.unlink(self.path)

    def test_get_actions_for_order_empty(self):
        self.assertEqual(self.store.get_actions_for_order("MISSING"), [])

    def test_get_all_actions_newest_first(self):
        for i in range(5):
            self.store.log_action(f"ORD-{i:03d}", "viewed")
        rows = self.store.get_all_actions(limit=10)
        self.assertEqual(len(rows), 5)
        # newest first → order IDs should be in descending creation order
        order_ids = [r[0] for r in rows]
        self.assertEqual(order_ids, list(reversed([f"ORD-{i:03d}" for i in range(5)])))

    def test_get_all_actions_limit(self):
        for i in range(20):
            self.store.log_action(f"ORD-{i:03d}", "viewed")
        rows = self.store.get_all_actions(limit=5)
        self.assertEqual(len(rows), 5)


if __name__ == "__main__":
    unittest.main()
