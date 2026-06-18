"""Tests for PrintQueueStore — persistent print queue with SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

from print_queue_store import (
    STATUS_ARCHIVED,
    STATUS_DESIGNED,
    STATUS_FAILED,
    STATUS_PRINTED,
    STATUS_QUEUED,
    STATUS_REQUEUED,
    PrintQueueItem,
    PrintQueueStore,
)


def _make_store() -> tuple[PrintQueueStore, str]:
    """Return a PrintQueueStore backed by a temporary file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return PrintQueueStore(db_path=path), path


class PrintQueueStoreInitTests(unittest.TestCase):
    def test_init_creates_table(self):
        store, path = _make_store()
        try:
            with sqlite3.connect(path) as con:
                cur = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='print_queue'"
                )
                self.assertIsNotNone(cur.fetchone())
        finally:
            os.unlink(path)

    def test_second_init_is_idempotent(self):
        store, path = _make_store()
        try:
            PrintQueueStore(db_path=path)  # re-init should not raise
        finally:
            os.unlink(path)


class PrintQueueStoreEnqueueTests(unittest.TestCase):
    def setUp(self):
        self.store, self.path = _make_store()

    def tearDown(self):
        os.unlink(self.path)

    def test_enqueue_returns_id(self):
        item_id = self.store.enqueue(
            source_type="file",
            source="/tmp/photo.jpg",
            display_name="photo.jpg",
            product="4x6 Print",
            size_key="4x6",
        )
        self.assertIsInstance(item_id, int)
        self.assertGreater(item_id, 0)

    def test_enqueue_multiple_auto_increment(self):
        id1 = self.store.enqueue(source_type="file", display_name="a.jpg")
        id2 = self.store.enqueue(source_type="file", display_name="b.jpg")
        self.assertNotEqual(id1, id2)

    def test_enqueue_default_status_is_queued(self):
        item_id = self.store.enqueue(source_type="file", display_name="test.jpg")
        item = self.store.get_item(item_id)
        self.assertEqual(item.status, STATUS_QUEUED)

    def test_enqueue_stores_render_settings(self):
        settings = {"crop_scale": 1.2, "crop_offset_x": 10}
        item_id = self.store.enqueue(
            source_type="file",
            display_name="photo.jpg",
            render_settings=settings,
        )
        item = self.store.get_item(item_id)
        self.assertEqual(item.render_settings["crop_scale"], 1.2)
        self.assertEqual(item.render_settings["crop_offset_x"], 10)

    def test_enqueue_stores_order_id(self):
        item_id = self.store.enqueue(
            source_type="url",
            display_name="Order 101",
            order_id="101",
        )
        item = self.store.get_item(item_id)
        self.assertEqual(item.order_id, "101")

    def test_enqueue_button_source_type(self):
        specs = {"outer_diameter": "1200", "text": "Hello"}
        item_id = self.store.enqueue(
            source_type="button",
            source="/tmp/photo.jpg",
            display_name="Button - photo.jpg",
            product="Button",
            size_key="button",
            render_settings={"button_specs": specs},
        )
        item = self.store.get_item(item_id)
        self.assertEqual(item.source_type, "button")
        self.assertEqual(item.render_settings["button_specs"]["text"], "Hello")


class PrintQueueStoreStatusTests(unittest.TestCase):
    def setUp(self):
        self.store, self.path = _make_store()
        self.item_id = self.store.enqueue(source_type="file", display_name="test.jpg")

    def tearDown(self):
        os.unlink(self.path)

    def test_mark_printed(self):
        self.store.mark_printed(self.item_id)
        item = self.store.get_item(self.item_id)
        self.assertEqual(item.status, STATUS_PRINTED)
        self.assertTrue(item.printed)
        self.assertNotEqual(item.printed_at, "")

    def test_mark_failed(self):
        self.store.mark_failed(self.item_id, "Printer offline")
        item = self.store.get_item(self.item_id)
        self.assertEqual(item.status, STATUS_FAILED)
        self.assertIn("Printer offline", item.last_error)

    def test_archive(self):
        self.store.archive(self.item_id)
        item = self.store.get_item(self.item_id)
        self.assertEqual(item.status, STATUS_ARCHIVED)
        self.assertNotEqual(item.archived_at, "")

    def test_requeue(self):
        self.store.mark_printed(self.item_id)
        self.store.requeue(self.item_id)
        item = self.store.get_item(self.item_id)
        self.assertEqual(item.status, STATUS_REQUEUED)
        self.assertFalse(item.printed)
        self.assertEqual(item.printed_at, "")

    def test_increment_reprint_count(self):
        self.store.increment_reprint_count(self.item_id)
        self.store.increment_reprint_count(self.item_id)
        item = self.store.get_item(self.item_id)
        self.assertEqual(item.reprint_count, 2)

    def test_update_render_settings(self):
        new_settings = {"crop_scale": 1.5, "crop_offset_x": -20, "crop_offset_y": 5}
        self.store.update_render_settings(self.item_id, new_settings)
        item = self.store.get_item(self.item_id)
        self.assertEqual(item.render_settings["crop_scale"], 1.5)
        self.assertEqual(item.render_settings["crop_offset_x"], -20)

    def test_apply_button_design(self):
        self.store.apply_button_design(
            self.item_id,
            render_settings={"button_specs": {"text": "Hi"}},
            product="Button",
            size_key="button",
        )
        item = self.store.get_item(self.item_id)
        self.assertEqual(item.status, STATUS_DESIGNED)
        self.assertEqual(item.size_key, "button")
        self.assertEqual(item.render_settings["button_specs"]["text"], "Hi")


class PrintQueueStoreQueryTests(unittest.TestCase):
    def setUp(self):
        self.store, self.path = _make_store()
        self.id_queued = self.store.enqueue(source_type="file", display_name="q.jpg")
        self.id_printed = self.store.enqueue(source_type="file", display_name="p.jpg")
        self.store.mark_printed(self.id_printed)
        self.id_archived = self.store.enqueue(source_type="file", display_name="a.jpg")
        self.store.archive(self.id_archived)

    def tearDown(self):
        os.unlink(self.path)

    def test_get_active_queue_excludes_archived(self):
        items = self.store.get_active_queue()
        ids = [i.id for i in items]
        self.assertIn(self.id_queued, ids)
        self.assertIn(self.id_printed, ids)
        self.assertNotIn(self.id_archived, ids)

    def test_get_queued_only(self):
        items = self.store.get_queued_only()
        ids = [i.id for i in items]
        self.assertIn(self.id_queued, ids)
        self.assertNotIn(self.id_printed, ids)
        self.assertNotIn(self.id_archived, ids)

    def test_get_queued_only_includes_designed_and_requeued(self):
        id_designed = self.store.enqueue(source_type="file", display_name="d.jpg")
        self.store.apply_button_design(id_designed, render_settings={"button_specs": {"text": "x"}})
        id_requeued = self.store.enqueue(source_type="file", display_name="r.jpg")
        self.store.mark_printed(id_requeued)
        self.store.requeue(id_requeued)

        items = self.store.get_queued_only()
        ids = [i.id for i in items]
        self.assertIn(id_designed, ids)
        self.assertIn(id_requeued, ids)

    def test_get_archived(self):
        items = self.store.get_archived()
        ids = [i.id for i in items]
        self.assertIn(self.id_archived, ids)
        self.assertNotIn(self.id_queued, ids)

    def test_get_all_returns_all(self):
        items = self.store.get_all()
        ids = [i.id for i in items]
        self.assertIn(self.id_queued, ids)
        self.assertIn(self.id_printed, ids)
        self.assertIn(self.id_archived, ids)

    def test_get_item_nonexistent_returns_none(self):
        self.assertIsNone(self.store.get_item(99999))


class PrintQueueItemHelperTests(unittest.TestCase):
    def _make_item(self, source_type="file", size_key="4x6"):
        return PrintQueueItem(
            source_type=source_type,
            size_key=size_key,
            display_name="test",
            render_settings={"key": "val"},
        )

    def test_is_button_true(self):
        item = self._make_item(source_type="button", size_key="button")
        self.assertTrue(item.is_button)

    def test_is_button_false(self):
        item = self._make_item(source_type="file", size_key="4x6")
        self.assertFalse(item.is_button)

    def test_is_button_true_for_button_size_key(self):
        item = self._make_item(source_type="url", size_key="button")
        self.assertTrue(item.is_button)

    def test_is_photo_true_4x6(self):
        item = self._make_item(size_key="4x6")
        self.assertTrue(item.is_photo)

    def test_is_photo_true_5x7(self):
        item = self._make_item(size_key="5x7")
        self.assertTrue(item.is_photo)

    def test_is_photo_true_8x10(self):
        item = self._make_item(size_key="8x10")
        self.assertTrue(item.is_photo)

    def test_is_photo_false_button(self):
        item = self._make_item(source_type="button", size_key="button")
        self.assertFalse(item.is_photo)

    def test_render_settings_json_round_trip(self):
        item = self._make_item()
        item.render_settings = {"a": 1, "b": "hello"}
        parsed = json.loads(item.render_settings_json())
        self.assertEqual(parsed["a"], 1)
        self.assertEqual(parsed["b"], "hello")


if __name__ == "__main__":
    unittest.main()
