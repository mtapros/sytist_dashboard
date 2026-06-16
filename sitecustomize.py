"""Runtime compatibility patch for the Sytist dashboard.

This repository's main entry point currently references
``SytistDashboard.load_sql_file`` while building the Tkinter UI, but the method
is missing from ``sytist.py``.  Python imports ``sitecustomize`` automatically
at startup when this file is on ``sys.path`` (which is the normal case when
running ``python sytist.py`` from this directory), so this patch installs the
missing method immediately after ``SytistDashboard`` is defined and before the
app is instantiated.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)


def _load_sql_file(self) -> None:
    """Prompt for an offline Sytist SQL dump and load it into the dashboard."""
    from tkinter import filedialog, messagebox

    filepath = filedialog.askopenfilename(
        title="Select Sytist SQL Dump",
        filetypes=[
            ("SQL dumps or zip archives", "*.sql *.zip"),
            ("SQL files", "*.sql"),
            ("Zip archives", "*.zip"),
            ("All files", "*.*"),
        ],
    )
    if not filepath:
        return

    try:
        orders, cart_items, photo_paths, status_lookup = self.data_loader.load_sql_dump(filepath)
        self.set_data(orders, cart_items, photo_paths, status_lookup)
        self.save_current_domain_to_selected_preset()
        self.save_config()
        messagebox.showinfo(
            "Success",
            f"Loaded {len(self.orders)} order{'s' if len(self.orders) != 1 else ''} "
            f"from {os.path.basename(filepath)}.",
        )
    except Exception as exc:  # pragma: no cover - UI error path
        logger.exception("Failed to load SQL dump: %s", filepath)
        messagebox.showerror("SQL Load Error", str(exc))


def _patch_sytist_dashboard(frame, event, arg):
    """Attach the missing method once ``SytistDashboard`` exists."""
    if event != "line":
        return _patch_sytist_dashboard

    dashboard_class = frame.f_globals.get("SytistDashboard")
    if dashboard_class is not None:
        if not hasattr(dashboard_class, "load_sql_file"):
            dashboard_class.load_sql_file = _load_sql_file
        sys.settrace(None)
        return None

    return _patch_sytist_dashboard


sys.settrace(_patch_sytist_dashboard)
