import json
import os
from copy import deepcopy
from datetime import datetime

DEFAULT_STATE = {"orders": {}}


class DashboardStateStore:
    def __init__(self, path: str):
        self.path = path

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return self._normalize(data)
            except Exception as e:
                print("Could not load dashboard state:", e)
        return deepcopy(DEFAULT_STATE)

    def save(self, state):
        data = self._normalize(state)
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print("Could not save dashboard state:", e)

    def get_order_state(self, state, order_id: str):
        orders = state.setdefault("orders", {})
        return orders.setdefault(str(order_id), self._default_order_state())

    def update_order_state(self, state, order_id: str, **kwargs):
        order_state = self.get_order_state(state, order_id)
        order_state.update(kwargs)
        order_state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return order_state

    @staticmethod
    def _default_order_state():
        return {
            "dashboard_status": "New",
            "notes": "",
            "reviewed": False,
            "flagged": False,
            "updated_at": "",
            "last_seen_sytist_status_id": "",
            "last_seen_sytist_status_name": "",
            "last_seen_payment_status": "",
        }

    def _normalize(self, data):
        data = data or {}
        data.setdefault("orders", {})
        for order_id, item in list(data["orders"].items()):
            normalized = self._default_order_state()
            if isinstance(item, dict):
                normalized.update(item)
            data["orders"][str(order_id)] = normalized
        return data
