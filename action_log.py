"""Local SQLite store for per-order workflow action logs.

Records actions performed by the tool for each order (printing, shipping, status
changes, etc.) with a timestamp so users have a persistent audit trail.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime


class ActionLogStore:
    """Persist and retrieve per-order action log entries in a local SQLite database."""

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
                CREATE TABLE IF NOT EXISTS order_actions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id     TEXT    NOT NULL,
                    action_type  TEXT    NOT NULL,
                    details      TEXT    NOT NULL DEFAULT '',
                    timestamp    TEXT    NOT NULL
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_order_actions_order_id "
                "ON order_actions (order_id)"
            )
            con.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log_action(
        self,
        order_id: str,
        action_type: str,
        details: str = "",
    ) -> None:
        """Insert an action log entry for *order_id*."""
        ts = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                "INSERT INTO order_actions (order_id, action_type, details, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (str(order_id), str(action_type), str(details or ""), ts),
            )
            con.commit()

    def log_actions_bulk(
        self,
        entries: list[tuple[str, str, str]],
    ) -> None:
        """Insert multiple ``(order_id, action_type, details)`` entries at once."""
        ts = datetime.now().isoformat(timespec="seconds")
        rows = [(str(oid), str(at), str(det or ""), ts) for oid, at, det in entries]
        if not rows:
            return
        with sqlite3.connect(self.db_path) as con:
            con.executemany(
                "INSERT INTO order_actions (order_id, action_type, details, timestamp) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
            con.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_actions_for_order(
        self, order_id: str
    ) -> list[tuple[str, str, str]]:
        """Return ``[(timestamp, action_type, details), ...]`` for *order_id*."""
        with sqlite3.connect(self.db_path) as con:
            cur = con.execute(
                "SELECT timestamp, action_type, details "
                "FROM order_actions WHERE order_id = ? ORDER BY id",
                (str(order_id),),
            )
            return cur.fetchall()

    def get_all_actions(self, limit: int = 500) -> list[tuple[str, str, str, str]]:
        """Return ``[(order_id, timestamp, action_type, details), ...]`` newest first."""
        with sqlite3.connect(self.db_path) as con:
            cur = con.execute(
                "SELECT order_id, timestamp, action_type, details "
                "FROM order_actions ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return cur.fetchall()
