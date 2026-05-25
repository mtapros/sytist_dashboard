# Sytist Order Dashboard

A Windows desktop application for managing photo orders from the
[Sytist](https://www.sytist.com/) e-commerce platform.  It can load orders
from a MySQL/MariaDB dump file **or** connect directly to a live database,
then lets you search, filter, and track orders, preview product images,
download photos organised into print-size folders, and send jobs directly to
Windows print queues.

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.10+ | `python.org` installer recommended; must include `tkinter` |
| Windows OS | Required for direct printing (`pywin32`); SQL-dump and preview features work on any OS |

### Install optional Python packages

```bash
pip install -r requirements.txt
```

| Package | Feature unlocked |
|---|---|
| `mysql-connector-python` | Live database connection |
| `Pillow` | Image preview & direct printing |
| `pywin32` | Direct printing to Windows printer queues |
| `keyring` | Secure OS-keyring storage for database passwords |

If a package is missing, the app still starts; affected features show an
install prompt when you try to use them.

---

## Running the app

```bash
python sytist.py
```

---

## File overview

| File | Purpose |
|---|---|
| `sytist.py` | Main Tkinter UI (`SytistDashboard`) |
| `models.py` | Data-classes: `Order`, `CartItem`, `PhotoPath`, `PrintJob` |
| `data_loader.py` | SQL dump parser + live MySQL loader (`SytistDataLoader`) |
| `config_store.py` | JSON config file manager (DB presets, printer routes, domains) |
| `dashboard_state.py` | Per-order workflow state stored in `dashboard_state.json` |
| `export_service.py` | Download photos and organise them into print-size folders |
| `printing_service.py` | Win32/Pillow direct-print execution |
| `dialogs.py` | Reusable Tkinter dialog windows |

---

## Configuration

On first run, `sytist_config.json` is created in the working directory.  It
stores:

- **Domain** — the base URL of your Sytist website (e.g. `https://yoursite.com`)
- **DB presets** — host, database name, and username for each MySQL server
  (passwords are stored in the OS keyring via the `keyring` package and are
  **never** written to the JSON file)
- **Printer routes** — maps print sizes (`4x6`, `5x7`, `8x10`, …) to Windows
  printer queue names

### Expected Sytist database tables

| Table | Used for |
|---|---|
| `ms_orders` | Order records |
| `ms_cart` | Line items per order |
| `ms_photos` or `ms_pic` | Photo file paths |
| `ms_order_status` | Order status label lookup |

The schema follows standard Sytist 3.x column naming conventions (e.g.
`order_ship_addres_2` — note the single `s` — is a known typo in the Sytist
schema that this app mirrors).

---

## SQL dump loading

Use **Load Offline .sql File** to load a `mysqldump` export from either a
plain `.sql` file or a `.zip` archive that contains a SQL dump.  The parser
handles standard mysqldump output with `CREATE TABLE` + `INSERT INTO` blocks.
It tracks quote and escape state, so values containing commas, single quotes,
or newlines are parsed correctly.

---

## Security notes

- The app connects to MySQL in **read-only** mode (`SET SESSION TRANSACTION
  READ ONLY`).
- Database passwords are stored in the OS keyring (Windows Credential
  Manager, macOS Keychain, or the system's `libsecret` on Linux) when the
  `keyring` package is installed.  Without `keyring`, passwords are held in
  memory for the session only and must be re-entered each time.
- Image downloads use a plain `User-Agent` header; no authentication
  credentials are sent to the photo server.

---

## Logging

The application uses Python's standard `logging` module.  To see debug
output, set the log level before launching:

```bash
python -c "import logging; logging.basicConfig(level=logging.DEBUG)" -c "exec(open('sytist.py').read())"
```

Or add `logging.basicConfig(level=logging.DEBUG)` to the `if __name__ ==
"__main__":` block in `sytist.py`.
