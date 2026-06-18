import json
import os
from copy import deepcopy

DEFAULT_DOMAIN = ""
DEFAULT_PRESET_NAME = "Default"

DEFAULT_CONFIG = {
    "domain": DEFAULT_DOMAIN,
    "domain_favorites": [],
    "selected_preset": DEFAULT_PRESET_NAME,
    "db_presets": {
        DEFAULT_PRESET_NAME: {
            "domain": DEFAULT_DOMAIN,
            "host": "",
            "db_name": "",
            "db_user": "",
        }
    },
    "printer_routes": {
        "4x6": "",
        "4x5": "",
        "5x7": "",
        "8x10": "",
        "wallet": "",
        "button": "",
        "magnet": "",
        "7in": "",
        "10in": ""
    },
    "usps": {
        "enabled": False,
        "base_url": "https://api.usps.com",
        "token_url": "",
        "environment": "production",
        "client_id": "",
        "client_secret": "",
        "timeout_seconds": 20,
        "ship_from": {
            "full_name": "",
            "address_1": "",
            "address_2": "",
            "city": "",
            "state": "",
            "postal_code": "",
            "country": "US",
            "phone": "",
            "email": "",
        },
    },
    "mailing_label": {
        "selected_brand": "Default",
        "brands": {
            "Default": {
                "logo_path": "",
                "logo_scale": 1.0,
                "logo_x": 40,
                "logo_y": 40,
            }
        },
    },
}


class ConfigStore:
    def __init__(self, path: str):
        self.path = path

    def load(self):
        config = deepcopy(DEFAULT_CONFIG)
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._merge_config(config, saved)
            except Exception as e:
                print("Could not load config file:", e)
        self._normalize(config)
        return config

    def save(self, config):
        self._normalize(config)
        # Never write DB passwords to disk; they are managed via keyring.
        data = deepcopy(config)
        for preset in data.get("db_presets", {}).values():
            preset.pop("db_pass", None)
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print("Could not save config file:", e)

    def _merge_config(self, target, saved):
        for key, value in saved.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                target[key].update(value)
            else:
                target[key] = value

    def _normalize(self, config):
        config.setdefault("domain", DEFAULT_DOMAIN)
        config.setdefault("domain_favorites", [])
        config.setdefault("selected_preset", DEFAULT_PRESET_NAME)
        config.setdefault("db_presets", {})
        config.setdefault("printer_routes", {})
        config.setdefault("usps", {})
        config.setdefault("mailing_label", {})

        for key in ["4x6", "4x5", "5x7", "8x10", "wallet", "button", "magnet", "7in", "10in"]:
            config["printer_routes"].setdefault(key, "")

        usps = config["usps"]
        usps.setdefault("enabled", False)
        usps.setdefault("base_url", "https://api.usps.com")
        usps.setdefault("token_url", "")
        usps.setdefault("environment", "production")
        usps.setdefault("client_id", "")
        usps.setdefault("client_secret", "")
        usps.setdefault("timeout_seconds", 20)
        usps.setdefault("ship_from", {})
        ship_from = usps["ship_from"]
        ship_from.setdefault("full_name", "")
        ship_from.setdefault("address_1", "")
        ship_from.setdefault("address_2", "")
        ship_from.setdefault("city", "")
        ship_from.setdefault("state", "")
        ship_from.setdefault("postal_code", "")
        ship_from.setdefault("country", "US")
        ship_from.setdefault("phone", "")
        ship_from.setdefault("email", "")

        mailing_label = config["mailing_label"]
        mailing_label.setdefault("selected_brand", "Default")
        mailing_label.setdefault("brands", {})
        brands = mailing_label["brands"]
        if not isinstance(brands, dict):
            brands = {}
            mailing_label["brands"] = brands
        if not brands:
            brands["Default"] = {}
        for brand_name, brand_data in list(brands.items()):
            if not isinstance(brand_data, dict):
                brand_data = {}
                brands[brand_name] = brand_data
            brand_data.setdefault("logo_path", "")
            try:
                brand_data["logo_scale"] = max(0.1, min(5.0, float(brand_data.get("logo_scale", 1.0))))
            except (TypeError, ValueError):
                brand_data["logo_scale"] = 1.0
            try:
                brand_data["logo_x"] = int(round(float(brand_data.get("logo_x", 40))))
            except (TypeError, ValueError):
                brand_data["logo_x"] = 40
            try:
                brand_data["logo_y"] = int(round(float(brand_data.get("logo_y", 40))))
            except (TypeError, ValueError):
                brand_data["logo_y"] = 40

        selected_brand = str(mailing_label.get("selected_brand", "")).strip()
        if not selected_brand or selected_brand not in brands:
            mailing_label["selected_brand"] = next(iter(brands.keys()))

        legacy_has_fields = any(key in config for key in ["host", "db_name", "db_user", "db_pass"])
        if legacy_has_fields:
            selected = config.get("selected_preset") or DEFAULT_PRESET_NAME
            config["db_presets"].setdefault(selected, {
                "domain": config.get("domain", DEFAULT_DOMAIN),
                "host": config.get("host", ""),
                "db_name": config.get("db_name", ""),
                "db_user": config.get("db_user", ""),
                # db_pass is intentionally not migrated to disk; callers handle
                # keyring migration when the user next connects.
            })

        if not config["db_presets"]:
            config["db_presets"][DEFAULT_PRESET_NAME] = deepcopy(DEFAULT_CONFIG["db_presets"][DEFAULT_PRESET_NAME])

        domain_favorites = []
        for domain in config.get("domain_favorites", []):
            domain = str(domain).strip()
            if domain and domain not in domain_favorites:
                domain_favorites.append(domain)

        for preset in config["db_presets"].values():
            preset.setdefault("domain", DEFAULT_DOMAIN)
            preset.setdefault("host", "")
            preset.setdefault("db_name", "")
            preset.setdefault("db_user", "")
            # db_pass is held in memory only (loaded from keyring by the UI layer);
            # it is never written to the JSON config file.
            preset.setdefault("db_pass", "")
            preset_domain = str(preset.get("domain", "")).strip()
            if preset_domain and preset_domain not in domain_favorites:
                domain_favorites.append(preset_domain)

        current_domain = str(config.get("domain", DEFAULT_DOMAIN)).strip()
        if current_domain and current_domain not in domain_favorites:
            domain_favorites.append(current_domain)

        config["domain_favorites"] = domain_favorites

        selected = config.get("selected_preset")
        if selected not in config["db_presets"]:
            selected = next(iter(config["db_presets"].keys()))
            config["selected_preset"] = selected

        selected_preset = config["db_presets"][selected]
        selected_preset["domain"] = str(selected_preset.get("domain") or current_domain).strip() or current_domain
        config["domain"] = selected_preset["domain"]


    def _normalize_preset(self, preset):
        preset = dict(preset or {})
        preset.setdefault("domain", "")
        preset.setdefault("host", "")
        preset.setdefault("db_name", "")
        preset.setdefault("db_user", "")
        preset.setdefault("db_pass", "")
        preset.setdefault("zoho_accounts_domain", "https://accounts.zoho.com")
        preset.setdefault("zoho_api_domain", "https://www.zohoapis.com")
        preset.setdefault("zoho_client_id", "")
        preset.setdefault("zoho_client_secret", "")
        preset.setdefault("zoho_refresh_token", "")
        preset.setdefault("zoho_organization_id", "")
        preset.setdefault("zoho_prefix", "")
        return preset
