"""Local SQLite store for user-defined product-type → action mappings.

When an unknown product type appears (i.e. one the printing service cannot
automatically classify), the user can define what to do with it:

* ``print_size`` — map to a known size key (e.g. "4x6", "5x7")
* ``skip``       — ignore / exclude from print/folder operations
* ``custom``     — a user-defined label / folder name

Mappings are persisted across sessions so the same choice is applied
automatically next time the product type appears.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

ACTION_PRINT_SIZE = "print_size"
ACTION_SKIP = "skip"
ACTION_CUSTOM = "custom"

VALID_ACTIONS = {ACTION_PRINT_SIZE, ACTION_SKIP, ACTION_CUSTOM}


class ProductTypeManager:
    """Persist and retrieve user-defined product-type mappings in SQLite."""

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
                CREATE TABLE IF NOT EXISTS product_type_mappings (
                    product_type TEXT PRIMARY KEY,
                    action       TEXT NOT NULL,
                    value        TEXT NOT NULL DEFAULT '',
                    updated_at   TEXT NOT NULL
                )
                """
            )
            con.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def set_mapping(
        self,
        product_type: str,
        action: str,
        value: str = "",
    ) -> None:
        """Create or replace the mapping for *product_type*."""
        if action not in VALID_ACTIONS:
            raise ValueError(f"action must be one of {VALID_ACTIONS!r}")
        ts = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """
                INSERT OR REPLACE INTO product_type_mappings
                    (product_type, action, value, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(product_type), action, str(value or ""), ts),
            )
            con.commit()

    def delete_mapping(self, product_type: str) -> None:
        """Remove the mapping for *product_type* (if it exists)."""
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                "DELETE FROM product_type_mappings WHERE product_type = ?",
                (str(product_type),),
            )
            con.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_mapping(self, product_type: str) -> dict | None:
        """Return ``{"action": ..., "value": ...}`` or ``None`` if not mapped."""
        with sqlite3.connect(self.db_path) as con:
            cur = con.execute(
                "SELECT action, value FROM product_type_mappings WHERE product_type = ?",
                (str(product_type),),
            )
            row = cur.fetchone()
        if row:
            return {"action": row[0], "value": row[1]}
        return None

    def get_all_mappings(self) -> list[tuple[str, str, str, str]]:
        """Return ``[(product_type, action, value, updated_at), ...]`` sorted by name."""
        with sqlite3.connect(self.db_path) as con:
            cur = con.execute(
                "SELECT product_type, action, value, updated_at "
                "FROM product_type_mappings ORDER BY product_type"
            )
            return cur.fetchall()

    def is_mapped(self, product_type: str) -> bool:
        """Return ``True`` if *product_type* has a saved mapping."""
        return self.get_mapping(product_type) is not None
