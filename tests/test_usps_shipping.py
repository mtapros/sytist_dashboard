import json
import os
import tempfile
import unittest
from unittest import mock

from config_store import ConfigStore
from dashboard_state import DashboardStateStore
from models import ShippingAddress
from usps_service import USPSNotConfiguredError, USPSService


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class USPSConfigAndStateTests(unittest.TestCase):
    def test_config_store_adds_usps_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.json")
            config = ConfigStore(path).load()

        self.assertIn("usps", config)
        self.assertFalse(config["usps"]["enabled"])
        self.assertEqual(config["usps"]["base_url"], "https://api.usps.com")
        self.assertEqual(config["usps"]["ship_from"]["country"], "US")
        self.assertIn("4x5", config["printer_routes"])
        self.assertIn("mailing_label", config)
        self.assertIn("Default", config["mailing_label"]["brands"])
        self.assertEqual(config["mailing_label"]["brands"]["Default"]["logo_scale"], 1.0)

    def test_dashboard_state_defaults_include_usps_shipment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"orders": {"1001": {"notes": "hello"}}}, fh)

            store = DashboardStateStore(path)
            data = store.load()

        self.assertIn("1001", data["orders"])
        self.assertEqual(data["orders"]["1001"]["usps_shipment"], {})


class USPSServiceTests(unittest.TestCase):
    def test_not_configured_raises_clear_error(self):
        service = USPSService({"usps": {"enabled": False}})
        with self.assertRaises(USPSNotConfiguredError):
            service.get_access_token()

    def test_validate_and_tracking_use_oauth_token(self):
        calls = []

        def fake_urlopen(request, timeout=20):
            calls.append((request.full_url, request.headers, request.data))
            if request.full_url.endswith("/oauth2/v3/token"):
                return _FakeResponse({"access_token": "token-123", "expires_in": 1800})
            if request.full_url.endswith("/addresses/v3/address"):
                auth_header = request.headers.get("Authorization", "")
                self.assertTrue(auth_header.startswith("Bearer "))
                return _FakeResponse({"validated": True})
            if "/tracking/v3/tracking/" in request.full_url:
                auth_header = request.headers.get("Authorization", "")
                self.assertTrue(auth_header.startswith("Bearer "))
                return _FakeResponse({"trackingNumber": "9400TEST", "status": "In Transit"})
            raise AssertionError(f"Unexpected URL: {request.full_url}")

        config = {
            "usps": {
                "enabled": True,
                "base_url": "https://example.usps.test",
                "token_url": "",
                "client_id": "id",
                "client_secret": "secret",
                "timeout_seconds": 5,
                "ship_from": {},
            }
        }
        service = USPSService(config)
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validated = service.validate_address(
                ShippingAddress(full_name="Jane Doe", address_1="123 Main", city="A", state="NY", postal_code="10001")
            )
            tracking = service.get_tracking("9400TEST")

        self.assertTrue(validated["validated"])
        self.assertEqual(tracking["trackingNumber"], "9400TEST")
        token_calls = [url for url, _, _ in calls if url.endswith("/oauth2/v3/token")]
        self.assertEqual(len(token_calls), 1)


if __name__ == "__main__":
    unittest.main()
