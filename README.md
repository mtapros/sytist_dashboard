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
| `usps_service.py` | USPS OAuth/API service wrapper (address/rates/labels/tracking) |
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
- **USPS settings** — OAuth client credentials, API base/token URLs, timeout,
  and a default ship-from/return address used by label requests
- **Mailing-label brands** — per-brand logo PNG path plus logo scale and X/Y
  placement used by the 4x6 landscape address-label preview/print flow

### USPS shipping (MVP)

The app includes an initial USPS workflow:

- **USPS Setup** button to configure USPS cloud API settings
- **USPS Ship Selected** button to open shipping actions for one selected order
- Destination prefill from Sytist order shipping fields
- **Print 4x6 Address** button to print a centered 4x6 address card, either
  prefilled from one selected order or entered manually
- API actions scaffolded for:
  - address validation
  - domestic rates
  - label creation
  - tracking lookup
- Shipment metadata is saved in `dashboard_state.json` per order

Required USPS setup:

1. Create an app in the USPS developer portal.
2. Enable the APIs your account is approved for.
3. Enter OAuth client ID/client secret in **USPS Setup**.
4. Confirm API base/token URLs for your USPS environment (production/sandbox).

MVP limitations:

- USPS cloud API products can be quota/approval-gated; unavailable products
  will return API errors and are surfaced in the UI.
- Endpoint payloads may require account-specific fields; this MVP is a safe
  integration scaffold with clear error handling, not a guaranteed full
  purchase flow for every USPS account out of the box.
- Credentials are stored in your local config file; do not commit secrets.

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

## Workflow overview

### 1 — Load data
Use **Load Offline .sql File** or connect to a live database.  After loading,
the main window shows a summary of how many orders were loaded.

### 2 — Open Orders Window
Click **Open Orders Window** to open the split-pane Orders view.  The top pane
lists orders (with ☐/☑ checkboxes for selection), and the bottom pane lists the
items for whichever order is highlighted.  From here you can:

- **Search** orders by ID or name.
- **Mark Reviewed** any checked orders.
- **Double-click** an order row to open its detail window.
- **Click a file name** in the items list to open the Image Preview window.
- **Refresh** to reload orders from the in-memory dataset.

### 3 — Order detail window
Double-clicking an order in the Orders window opens a full detail view.  Click
a file name in the bottom items table to preview its image in the Image Preview
window.  The detail window also has an **Action Log** button showing all
recorded workflow events for that order.

### 4 — Image Preview window
Images (URLs or local file paths) open in a separate, dedicated window.  The
window shows a clickable URL at the top and renders the image below.

### 5 — Print, ship, and export
Use the action buttons on the main window to print orders, generate print
folders, ship via USPS, or push invoices to Zoho Books.  All of these actions
are recorded in the action log database (`sytist_actions.db`).

---

## Product Type Manager

When you click **Product Types** (in the Printing section of the main window)
or attempt to print orders that include **unknown product types**, the Product
Type Manager workflow activates.

- **Unknown product types** are those not automatically recognised by the
  printing service from their name or associated file name.
- For each unknown type you can choose:
  - **Print: &lt;size&gt;** — assign a standard print size (`4x6`, `5x7`, `8x10`, etc.).
  - **skip** — exclude items of this type from printing/folder generation.
  - **custom label** — assign any folder/label name you like.
- Mappings are saved to `sytist_actions.db` and applied automatically the next
  time the same product type appears.
- You can view, add, edit, and delete all mappings via the **Product Types**
  button on the main window.

---

## Action Log (local DB persistence)

All workflow actions are recorded in a local SQLite database (`sytist_actions.db`
in the working directory).  Recorded events include:

| Event | Trigger |
|---|---|
| `viewed` | Opening an order's detail window |
| `status_updated` | Saving dashboard fields for an order |
| `printed` | Printing one or more orders |
| `generate_print_folders` | Generating print-size folders |
| `zoho_push` / `zoho_push_error` | Pushing an order to Zoho Books |
| `shipping_label_created` | Creating a USPS shipping label |
| `tracking_checked` | Looking up a USPS tracking number |

View the log for any order via the **Action Log** button in its detail window.



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
