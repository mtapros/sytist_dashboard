import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict

from models import PackageDetails, ShippingAddress


class USPSServiceError(Exception):
    pass


class USPSNotConfiguredError(USPSServiceError):
    pass


class USPSService:
    def __init__(self, config: dict):
        self.config = config or {}
        self._access_token = ""
        self._access_token_expires_at = 0.0

    def _usps_config(self):
        return self.config.get("usps", {}) if isinstance(self.config, dict) else {}

    def is_configured(self):
        usps = self._usps_config()
        return bool(usps.get("enabled") and str(usps.get("client_id", "")).strip() and str(usps.get("client_secret", "")).strip())

    def _require_configured(self):
        if not self.is_configured():
            raise USPSNotConfiguredError(
                "USPS is not configured. Open USPS Setup and provide enabled, client ID, and client secret."
            )

    def _timeout(self):
        usps = self._usps_config()
        try:
            timeout = int(usps.get("timeout_seconds", 20))
            return timeout if timeout > 0 else 20
        except Exception:
            return 20

    def _base_url(self):
        usps = self._usps_config()
        return str(usps.get("base_url", "https://api.usps.com")).rstrip("/")

    def _token_url(self):
        usps = self._usps_config()
        token_url = str(usps.get("token_url", "")).strip()
        if token_url:
            return token_url
        return f"{self._base_url()}/oauth2/v3/token"

    @staticmethod
    def _safe_json_loads(raw_bytes):
        if not raw_bytes:
            return {}
        try:
            text = raw_bytes.decode("utf-8", errors="replace")
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"data": parsed}
        except Exception:
            return {"raw": raw_bytes.decode("utf-8", errors="replace")}

    def _http_request(self, method: str, url: str, *, headers=None, body=None):
        req = urllib.request.Request(url=url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout()) as response:
                payload = response.read()
                return self._safe_json_loads(payload)
        except urllib.error.HTTPError as exc:
            payload = self._safe_json_loads(exc.read())
            detail = payload.get("error_description") or payload.get("message") or payload.get("error") or str(payload)
            raise USPSServiceError(f"USPS API error ({exc.code}) for {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise USPSServiceError(f"Could not reach USPS API at {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise USPSServiceError(f"USPS API timeout for {url}") from exc
        except Exception as exc:
            raise USPSServiceError(f"Unexpected USPS API error for {url}: {exc}") from exc

    def get_access_token(self):
        self._require_configured()

        if self._access_token and time.time() < self._access_token_expires_at:
            return self._access_token

        usps = self._usps_config()
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": str(usps.get("client_id", "")).strip(),
                "client_secret": str(usps.get("client_secret", "")).strip(),
            }
        ).encode("utf-8")
        data = self._http_request(
            "POST",
            self._token_url(),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            body=body,
        )

        token = str(data.get("access_token", "")).strip()
        if not token:
            raise USPSServiceError("USPS token response did not include access_token.")
        expires_in = int(data.get("expires_in", 1200) or 1200)
        self._access_token = token
        self._access_token_expires_at = time.time() + max(60, expires_in - 30)
        return token

    def _authorized_headers(self):
        token = self.get_access_token()
        return {
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def validate_address(self, address: ShippingAddress | dict):
        self._require_configured()
        payload = asdict(address) if isinstance(address, ShippingAddress) else dict(address or {})
        endpoint = f"{self._base_url()}/addresses/v3/address"
        return self._http_request(
            "POST",
            endpoint,
            headers=self._authorized_headers(),
            body=json.dumps(payload).encode("utf-8"),
        )

    def get_domestic_rates(self, destination: ShippingAddress | dict, package: PackageDetails | dict):
        self._require_configured()
        payload = {
            "destination": asdict(destination) if isinstance(destination, ShippingAddress) else dict(destination or {}),
            "package": asdict(package) if isinstance(package, PackageDetails) else dict(package or {}),
        }
        endpoint = f"{self._base_url()}/prices/v3/base-rates/search"
        return self._http_request(
            "POST",
            endpoint,
            headers=self._authorized_headers(),
            body=json.dumps(payload).encode("utf-8"),
        )

    def create_label(
        self,
        destination: ShippingAddress | dict,
        package: PackageDetails | dict,
        selected_rate: dict | None = None,
    ):
        self._require_configured()
        ship_from = dict(self._usps_config().get("ship_from", {}) or {})
        payload = {
            "fromAddress": ship_from,
            "toAddress": asdict(destination) if isinstance(destination, ShippingAddress) else dict(destination or {}),
            "package": asdict(package) if isinstance(package, PackageDetails) else dict(package or {}),
            "selectedRate": selected_rate or {},
        }
        endpoint = f"{self._base_url()}/labels/v3/labels"
        return self._http_request(
            "POST",
            endpoint,
            headers=self._authorized_headers(),
            body=json.dumps(payload).encode("utf-8"),
        )

    def get_tracking(self, tracking_number: str):
        self._require_configured()
        tracking_number = str(tracking_number or "").strip()
        if not tracking_number:
            raise USPSServiceError("Tracking number is required.")
        quoted = urllib.parse.quote(tracking_number, safe="")
        endpoint = f"{self._base_url()}/tracking/v3/tracking/{quoted}"
        return self._http_request("GET", endpoint, headers=self._authorized_headers())
