"""Persistent print queue stored in the shared SQLite database (sytist_actions.db).

Each row represents one enqueued print job with enough metadata to reprint,
archive, and reconstruct the original job settings.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any


# Valid status values.
STATUS_QUEUED = "queued"
STATUS_PRINTED = "printed"
STATUS_FAILED = "failed"
STATUS_ARCHIVED = "archived"

_VALID_STATUSES = {STATUS_QUEUED, STATUS_PRINTED, STATUS_FAILED, STATUS_ARCHIVED}


class PrintQueueItem:
    """In-memory representation of a single print queue row."""

    __slots__ = (
        "id",
        "created_at",
        "updated_at",
        "status",
        "printed",
        "printed_at",
        "archived_at",
        "reprint_count",
        "last_error",
        "source_type",
        "source",
        "display_name",
        "product",
        "size_key",
        "order_id",
        "routed_printer",
        "render_settings",
    )

    def __init__(
        self,
        *,
        id: int | None = None,
        created_at: str = "",
        updated_at: str = "",
        status: str = STATUS_QUEUED,
        printed: bool = False,
        printed_at: str = "",
        archived_at: str = "",
        reprint_count: int = 0,
        last_error: str = "",
        source_type: str = "",
        source: str = "",
        display_name: str = "",
        product: str = "",
        size_key: str = "",
        order_id: str = "",
        routed_printer: str = "",
        render_settings: dict[str, Any] | None = None,
    ) -> None:
        self.id = id
        self.created_at = created_at
        self.updated_at = updated_at
        self.status = status
        self.printed = printed
        self.printed_at = printed_at
        self.archived_at = archived_at
        self.reprint_count = reprint_count
        self.last_error = last_error
        self.source_type = source_type
        self.source = source
        self.display_name = display_name
        self.product = product
        self.size_key = size_key
        self.order_id = order_id
        self.routed_printer = routed_printer
        self.render_settings = render_settings or {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def render_settings_json(self) -> str:
        return json.dumps(self.render_settings) if self.render_settings else "{}"

    @property
    def is_button(self) -> bool:
        return self.source_type == "button" or self.size_key == "button"

    @property
    def is_photo(self) -> bool:
        return self.size_key in ("4x6", "5x7", "8x10", "4x5")


class PrintQueueStore:
    """Persist and retrieve print queue items in a local SQLite database."""

    def __init__(self, db_path: str = "sytist_actions.db") -> None:
        self.db_path = db_path
        self._init_db()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS print_queue (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at      TEXT    NOT NULL,
                    updated_at      TEXT    NOT NULL,
                    status          TEXT    NOT NULL DEFAULT 'queued',
                    printed         INTEGER NOT NULL DEFAULT 0,
                    printed_at      TEXT    NOT NULL DEFAULT '',
                    archived_at     TEXT    NOT NULL DEFAULT '',
                    reprint_count   INTEGER NOT NULL DEFAULT 0,
                    last_error      TEXT    NOT NULL DEFAULT '',
                    source_type     TEXT    NOT NULL DEFAULT '',
                    source          TEXT    NOT NULL DEFAULT '',
                    display_name    TEXT    NOT NULL DEFAULT '',
                    product         TEXT    NOT NULL DEFAULT '',
                    size_key        TEXT    NOT NULL DEFAULT '',
                    order_id        TEXT    NOT NULL DEFAULT '',
                    routed_printer  TEXT    NOT NULL DEFAULT '',
                    render_settings TEXT    NOT NULL DEFAULT '{}'
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_print_queue_status "
                "ON print_queue (status)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_print_queue_order_id "
                "ON print_queue (order_id)"
            )
            con.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def enqueue(
        self,
        *,
        source_type: str,
        source: str = "",
        display_name: str,
        product: str = "",
        size_key: str = "",
        order_id: str = "",
        routed_printer: str = "",
        render_settings: dict[str, Any] | None = None,
    ) -> int:
        """Insert a new queued item and return its id."""
        now = datetime.now().isoformat(timespec="seconds")
        settings_json = json.dumps(render_settings or {})
        with sqlite3.connect(self.db_path) as con:
            cur = con.execute(
                """
                INSERT INTO print_queue
                    (created_at, updated_at, status, printed, printed_at,
                     archived_at, reprint_count, last_error,
                     source_type, source, display_name, product, size_key,
                     order_id, routed_printer, render_settings)
                VALUES (?, ?, ?, 0, '', '', 0, '', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now, now, STATUS_QUEUED,
                    source_type, source, display_name, product, size_key,
                    order_id, routed_printer, settings_json,
                ),
            )
            con.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def mark_printed(self, item_id: int) -> None:
        """Mark an item as printed with the current timestamp."""
        now = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """
                UPDATE print_queue
                SET status = ?, printed = 1, printed_at = ?, updated_at = ?,
                    last_error = ''
                WHERE id = ?
                """,
                (STATUS_PRINTED, now, now, item_id),
            )
            con.commit()

    def mark_failed(self, item_id: int, error: str = "") -> None:
        """Mark an item as failed and record the error message."""
        now = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """
                UPDATE print_queue
                SET status = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (STATUS_FAILED, str(error or ""), now, item_id),
            )
            con.commit()

    def archive(self, item_id: int) -> None:
        """Archive an item (preferred over delete)."""
        now = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """
                UPDATE print_queue
                SET status = ?, archived_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (STATUS_ARCHIVED, now, now, item_id),
            )
            con.commit()

    def requeue(self, item_id: int) -> None:
        """Return a printed/failed/archived item to queued status."""
        now = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """
                UPDATE print_queue
                SET status = ?, archived_at = '', last_error = '', updated_at = ?
                WHERE id = ?
                """,
                (STATUS_QUEUED, now, item_id),
            )
            con.commit()

    def increment_reprint_count(self, item_id: int) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                "UPDATE print_queue SET reprint_count = reprint_count + 1, updated_at = ? WHERE id = ?",
                (now, item_id),
            )
            con.commit()

    def update_render_settings(self, item_id: int, render_settings: dict[str, Any]) -> None:
        """Replace the render_settings JSON for an existing item."""
        now = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                "UPDATE print_queue SET render_settings = ?, updated_at = ? WHERE id = ?",
                (json.dumps(render_settings), now, item_id),
            )
            con.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_item(self, item_id: int) -> PrintQueueItem | None:
        with sqlite3.connect(self.db_path) as con:
            con.row_factory = sqlite3.Row
            cur = con.execute("SELECT * FROM print_queue WHERE id = ?", (item_id,))
            row = cur.fetchone()
        return _row_to_item(row) if row else None

    def get_active_queue(self) -> list[PrintQueueItem]:
        """Return all non-archived items ordered by id ascending."""
        with sqlite3.connect(self.db_path) as con:
            con.row_factory = sqlite3.Row
            cur = con.execute(
                "SELECT * FROM print_queue WHERE status != ? ORDER BY id ASC",
                (STATUS_ARCHIVED,),
            )
            return [_row_to_item(r) for r in cur.fetchall()]

    def get_queued_only(self) -> list[PrintQueueItem]:
        """Return items with status='queued' ordered by id ascending."""
        with sqlite3.connect(self.db_path) as con:
            con.row_factory = sqlite3.Row
            cur = con.execute(
                "SELECT * FROM print_queue WHERE status = ? ORDER BY id ASC",
                (STATUS_QUEUED,),
            )
            return [_row_to_item(r) for r in cur.fetchall()]

    def get_archived(self) -> list[PrintQueueItem]:
        """Return archived items, newest first."""
        with sqlite3.connect(self.db_path) as con:
            con.row_factory = sqlite3.Row
            cur = con.execute(
                "SELECT * FROM print_queue WHERE status = ? ORDER BY id DESC",
                (STATUS_ARCHIVED,),
            )
            return [_row_to_item(r) for r in cur.fetchall()]

    def get_all(self) -> list[PrintQueueItem]:
        """Return all items, newest first."""
        with sqlite3.connect(self.db_path) as con:
            con.row_factory = sqlite3.Row
            cur = con.execute("SELECT * FROM print_queue ORDER BY id DESC")
            return [_row_to_item(r) for r in cur.fetchall()]


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _row_to_item(row: sqlite3.Row) -> PrintQueueItem:
    try:
        settings = json.loads(row["render_settings"] or "{}")
    except (json.JSONDecodeError, TypeError):
        settings = {}
    return PrintQueueItem(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        status=row["status"],
        printed=bool(row["printed"]),
        printed_at=row["printed_at"] or "",
        archived_at=row["archived_at"] or "",
        reprint_count=row["reprint_count"],
        last_error=row["last_error"] or "",
        source_type=row["source_type"],
        source=row["source"],
        display_name=row["display_name"],
        product=row["product"],
        size_key=row["size_key"],
        order_id=row["order_id"] or "",
        routed_printer=row["routed_printer"] or "",
        render_settings=settings,
    )
