import logging
import os
from contextlib import contextmanager
from io import TextIOWrapper
from zipfile import ZipFile

try:
    import mysql.connector
    HAS_MYSQL = True
except ImportError:
    HAS_MYSQL = False

from models import CartItem, Order, PhotoPath

logger = logging.getLogger(__name__)


class SytistDataLoader:
    ORDER_STATUS_QUERY = "SELECT * FROM ms_order_status"
    DEFAULT_STATUS_FALLBACKS = {
        "0": "Open",
        "1": "Archived",
        "2": "Trashed",
    }

    def __init__(self):
        self.order_status_lookup = {}

    def robust_parse(self, sql_string):
        results = []
        current_tuple = []
        current_val = ""
        in_quote = False
        in_tuple = False
        escaped = False

        for char in sql_string:
            if escaped:
                current_val += char
                escaped = False
                continue
            if char == "\\":
                current_val += char
                escaped = True
                continue
            if char == "'" and not escaped:
                in_quote = not in_quote
                continue
            if not in_quote:
                if char == "(":
                    if not in_tuple:
                        in_tuple = True
                        current_tuple = []
                        current_val = ""
                        continue
                elif char == ")":
                    if in_tuple:
                        current_tuple.append(current_val.strip())
                        results.append(current_tuple)
                        in_tuple = False
                        continue
                elif char == ",":
                    if in_tuple:
                        current_tuple.append(current_val.strip())
                        current_val = ""
                        continue
            if in_tuple:
                current_val += char
        return results

    @staticmethod
    def _clean(value):
        if value is None:
            return ""
        value = str(value)
        if value.upper() == "NULL":
            return ""
        return value

    @staticmethod
    def _columns_from_create(create_sql):
        columns = []
        if not create_sql:
            return columns
        for line in create_sql.splitlines():
            line = line.strip()
            if line.startswith("`"):
                columns.append(line.split("`")[1])
        return columns

    @staticmethod
    def _extract_create_table_name(line: str):
        marker = 'CREATE TABLE `'
        if not line.startswith(marker):
            return None
        rest = line[len(marker):]
        return rest.split("`", 1)[0]

    @contextmanager
    def _open_sql_stream(self, filepath):
        if os.path.splitext(filepath)[1].lower() != ".zip":
            with open(filepath, "r", encoding="utf-8", errors="ignore") as stream:
                yield stream
            return

        with ZipFile(filepath) as archive:
            sql_members = [
                info for info in archive.infolist()
                if not info.is_dir() and info.filename.lower().endswith(".sql")
            ]
            if not sql_members:
                raise ValueError("Zip archive does not contain a .sql file.")
            with archive.open(sql_members[0]) as raw_stream:
                with TextIOWrapper(raw_stream, encoding="utf-8", errors="ignore") as stream:
                    yield stream

    def _build_order(self, record, status_lookup):
        status_id = self._clean(
            record.get("order_status", "")
            or record.get("order_status_id", "")
            or record.get("status_id", "")
        )
        status_entry = status_lookup.get(status_id, {})
        status_name = self._clean(
            status_entry.get("status_name", "")
            or status_entry.get("name", "")
            or record.get("order_status_name", "")
            or record.get("status_name", "")
            or self.DEFAULT_STATUS_FALLBACKS.get(status_id, "")
        )
        first = self._clean(record.get("order_first_name", ""))
        last = self._clean(record.get("order_last_name", ""))
        return Order(
            id=self._clean(record.get("order_id", "")),
            name=f"{first} {last}".strip(),
            first=first,
            last=last,
            email=self._clean(record.get("order_email", "")),
            total=self._clean(record.get("order_total", "0.00")),
            date=self._clean(record.get("order_date", "")),
            phone=self._clean(record.get("order_phone", "")),
            payment_type=self._clean(record.get("order_pay_type", "")),
            payment_status=self._clean(record.get("order_payment_status", "")),
            payment_transaction=self._clean(record.get("order_pay_transaction", "")),
            payment_reference=self._clean(record.get("order_payment_reference", "")),
            payment_info=self._clean(record.get("order_payment_info", "")),
            status_id=status_id,
            status_name=status_name,
            shipping=self._clean(record.get("order_shipping", "0.00")),
            tax=self._clean(record.get("order_tax", "0.00")),
            fees=self._clean(record.get("order_fees", "0.00")),
            discount=self._clean(record.get("order_discount", "0.00")),
            subtotal=self._clean(record.get("order_sub_total", "0.00")),
            taxable_amount=self._clean(record.get("order_taxable_amount", "0.00")),
            tax_percentage=self._clean(record.get("order_tax_percentage", "0.0000")),
            ship_cost=self._clean(record.get("order_ship_cost", "0.00")),
            vat=self._clean(record.get("order_vat", "0.00")),
            vat_percentage=self._clean(record.get("order_vat_percentage", "0.0000")),
            payment_amount=self._clean(record.get("order_payment", "0.00")),
            payment_date=self._clean(record.get("order_payment_date", "")),
            credit=self._clean(record.get("order_credit", "0.00")),
            gift_certificate=self._clean(record.get("order_gift_certificate", "0.00")),
            gift_certificate_id=self._clean(record.get("order_gift_certificate_id", "")),
            order_fee=self._clean(record.get("order_fee", "0.00")),
            order_fee_name=self._clean(record.get("order_fee_name", "")),
            payment_fee=self._clean(record.get("order_payment_fee", "0.00")),
            payment_fee_name=self._clean(record.get("order_payment_fee_name", "")),
            shipped_by=self._clean(record.get("order_shipped_by", "")),
            shipped_date=self._clean(record.get("order_shipped_date", "")),
            shipped_track=self._clean(record.get("order_shipped_track", "")),
            shipping_option=self._clean(record.get("order_shipping_option", "")),
            address=self._clean(record.get("order_address", "")),
            address_2=self._clean(record.get("order_address_2", "")),
            city=self._clean(record.get("order_city", "")),
            state=self._clean(record.get("order_state", "")),
            zip_code=self._clean(record.get("order_zip", "")),
            country=self._clean(record.get("order_country", "")),
            ship_first_name=self._clean(record.get("order_ship_first_name", "")),
            ship_last_name=self._clean(record.get("order_ship_last_name", "")),
            ship_address=self._clean(record.get("order_ship_address", "")),
            ship_address_2=self._clean(record.get("order_ship_addres_2", "")),
            ship_city=self._clean(record.get("order_ship_city", "")),
            ship_state=self._clean(record.get("order_ship_state", "")),
            ship_zip=self._clean(record.get("order_ship_zip", "")),
            ship_country=self._clean(record.get("order_ship_country", "")),
            customer_notes=self._clean(record.get("order_notes", "")),
            admin_notes=self._clean(record.get("order_admin_notes", "")),
            short_url=self._clean(record.get("order_short_url", "")),
            card_last_four=self._clean(record.get("order_card_last_four", "")),
            raw_fields=record,
        )

    def _fetch_order_status_lookup_db(self, cursor):
        lookup = {}
        try:
            cursor.execute(self.ORDER_STATUS_QUERY)
            columns = [desc[0] for desc in getattr(cursor, "description", [])]
            for row in cursor.fetchall():
                if isinstance(row, dict):
                    record = {key: self._clean(value) for key, value in row.items()}
                else:
                    record = {
                        columns[idx]: self._clean(value)
                        for idx, value in enumerate(row)
                        if idx < len(columns)
                    }

                status_id = self._clean(
                    record.get("status_id", "")
                    or record.get("id", "")
                    or record.get("status", "")
                )
                if not status_id:
                    continue

                lookup[status_id] = {
                    "status_name": self._clean(
                        record.get("status_name", "")
                        or record.get("name", "")
                        or record.get("status_descr", "")
                        or record.get("description", "")
                    ),
                    "status_descr": self._clean(
                        record.get("status_descr", "")
                        or record.get("description", "")
                    ),
                    "status_show_order": self._clean(
                        record.get("status_show_order", "")
                        or record.get("show_order", "")
                        or record.get("sort_order", "")
                    ),
                }
        except Exception as exc:
            logger.warning("Could not load order status lookup from DB: %s", exc)
        self.order_status_lookup = lookup
        return lookup

    def _extract_status_override(self, table_name, record):
        if table_name != "ms_notes":
            return None

        message = self._clean(record.get("note_note", "") or record.get("note_title", "") or record.get("note_descr", ""))
        payload = self._clean(record.get("note_extra", ""))
        table_ref = self._clean(record.get("note_table", ""))
        table_id = self._clean(record.get("note_table_id", ""))

        if table_ref != "ms_orders" or "Order status changed to" not in message:
            return None

        status_name = message.split("Order status changed to", 1)[1].strip()
        order_id = table_id
        status_id = ""

        for line in payload.splitlines():
            line = line.strip()
            if line.startswith("order_id:::") and not order_id:
                order_id = self._clean(line.split(":::", 1)[1])
            elif line.startswith("status_id:::"):
                status_id = self._clean(line.split(":::", 1)[1])

        if not order_id:
            return None

        return order_id, {"status_id": status_id, "status_name": status_name}

    def _parse_insert_buffer(self, table_name, buffer, create_columns, orders, cart_items, photo_paths, status_lookup, status_overrides):
        parsed_tuples = self.robust_parse(buffer)
        cols = create_columns.get(table_name, [])

        if not cols:
            return

        for parts in parsed_tuples:
            record = {cols[i]: self._clean(parts[i]) for i in range(min(len(cols), len(parts)))}
            if not record:
                continue

            if table_name == "ms_orders":
                orders.append(self._build_order(record, status_lookup))
            elif table_name in ("ms_cart", "ms_cart_archive"):
                cart_items.append(
                    CartItem(
                        order_id=self._clean(record.get("cart_order", record.get("order_id", ""))),
                        product=self._clean(record.get("cart_product_name", record.get("cart_product", ""))),
                        qty=self._clean(record.get("cart_qty", "0")),
                        price=self._clean(record.get("cart_price", "0.00")),
                        file=self._clean(record.get("cart_pic_org", record.get("cart_file", ""))),
                        pic_id=self._clean(record.get("cart_pic_id", "")),
                    )
                )
            elif table_name == "ms_photos":
                pic_id = self._clean(record.get("pic_id", ""))
                if pic_id:
                    photo_paths[pic_id] = PhotoPath(
                        folder=self._clean(record.get("pic_folder", "")),
                        hashed_file=self._clean(record.get("pic_full", "")),
                        web_file=self._clean(record.get("pic_pic", "")),
                        large_file=self._clean(record.get("pic_large", "")),
                    )
            elif table_name == "ms_order_status":
                status_id = self._clean(record.get("status_id", ""))
                if status_id:
                    status_lookup[status_id] = {
                        "status_name": self._clean(record.get("status_name", "")),
                        "status_descr": self._clean(record.get("status_descr", "")),
                        "status_show_order": self._clean(record.get("status_show_order", "")),
                    }
            elif table_name == "ms_notes":
                override = self._extract_status_override(table_name, record)
                if override:
                    order_id, data = override
                    status_overrides[order_id] = data


    def _apply_status_overrides(self, orders, status_overrides):
        for order in orders:
            override = status_overrides.get(order.id)
            if override:
                if override.get("status_id"):
                    order.status_id = override["status_id"]
                if override.get("status_name"):
                    order.status_name = override["status_name"]
        return orders

    def load_sql_dump(self, filepath):
        orders = []
        cart_items = []
        photo_paths = {}
        status_lookup = {}
        status_overrides = {}
        create_columns = {}

        active_table = None
        buffer = ""
        in_quote = False
        escaped = False
        create_buffer = None
        create_table_name = None

        with self._open_sql_stream(filepath) as f:
            for line in f:
                if create_buffer is not None:
                    create_buffer.append(line)
                    if line.strip().startswith(") ENGINE="):
                        create_columns[create_table_name] = self._columns_from_create("".join(create_buffer))
                        create_buffer = None
                        create_table_name = None
                    continue

                maybe_create = self._extract_create_table_name(line)
                if maybe_create:
                    create_table_name = maybe_create
                    create_buffer = [line]
                    if line.strip().startswith(") ENGINE="):
                        create_columns[create_table_name] = self._columns_from_create("".join(create_buffer))
                        create_buffer = None
                        create_table_name = None
                    continue

                if active_table is None:
                    if line.startswith("INSERT INTO `ms_orders`"):
                        active_table = "ms_orders"
                    elif line.startswith("INSERT INTO `ms_cart`"):
                        active_table = "ms_cart"
                    elif line.startswith("INSERT INTO `ms_cart_archive`"):
                        active_table = "ms_cart_archive"
                    elif line.startswith("INSERT INTO `ms_photos`") or line.startswith("INSERT INTO `ms_pic`"):
                        active_table = "ms_photos"
                    elif line.startswith("INSERT INTO `ms_order_status`"):
                        active_table = "ms_order_status"
                    elif line.startswith("INSERT INTO `ms_notes`"):
                        active_table = "ms_notes"
                    else:
                        continue

                    buffer = line[line.find(" VALUES ") + 8:]
                    in_quote = False
                    escaped = False
                    seed_text = buffer
                else:
                    buffer += line
                    seed_text = line

                for char in seed_text:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == "'":
                        in_quote = not in_quote

                if active_table and not in_quote and line.strip().endswith(";"):
                    self._parse_insert_buffer(
                        active_table,
                        buffer,
                        create_columns,
                        orders,
                        cart_items,
                        photo_paths,
                        status_lookup,
                        status_overrides,
                    )
                    active_table = None
                    buffer = ""

        self.order_status_lookup = status_lookup
        if status_lookup:
            for order in orders:
                if not order.status_name:
                    order.status_name = status_lookup.get(order.status_id, {}).get("status_name", "")

        self._apply_status_overrides(orders, status_overrides)

        return orders, cart_items, photo_paths, status_lookup

    def load_live_db(self, host, user, password, database):
        if not HAS_MYSQL:
            raise RuntimeError("Please run: pip install mysql-connector-python")

        conn = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database,
        )

        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute("SET SESSION TRANSACTION READ ONLY;")
            except Exception:
                pass

            status_lookup = self._fetch_order_status_lookup_db(cursor)

            cursor.execute(
                """
                SELECT
                    order_id, order_date, order_total, order_email,
                    order_first_name, order_last_name, order_phone,
                    order_shipping, order_tax, order_fees, order_pay_type,
                    order_payment_status, order_pay_transaction, order_status,
                    order_discount, order_shipping_option, order_sub_total,
                    order_tax_percentage, order_taxable_amount, order_shipped_by,
                    order_shipped_date, order_shipped_track, order_ship_cost,
                    order_vat, order_vat_percentage, order_admin_notes,
                    order_payment, order_offline, order_payment_date,
                    order_payment_reference, order_payment_info, order_credit,
                    order_notes, order_short_url, order_card_last_four,
                    order_fee, order_fee_name, order_payment_fee,
                    order_payment_fee_name, order_address, order_address_2,
                    order_city, order_state, order_zip, order_country,
                    order_ship_first_name, order_ship_last_name, order_ship_address,
                    order_ship_addres_2, order_ship_city, order_ship_state,
                    order_ship_zip, order_ship_country, order_gift_certificate,
                    order_gift_certificate_id
                FROM ms_orders
                ORDER BY order_id DESC
                """
            )

            orders = [self._build_order({k: self._clean(v) for k, v in row.items()}, status_lookup) for row in cursor.fetchall()]

            cursor.execute(
                """
                SELECT cart_order, cart_product_name, cart_qty, cart_price, cart_pic_org, cart_pic_id
                FROM ms_cart
                """
            )

            cart_items = [
                CartItem(
                    order_id=self._clean(r.get("cart_order")),
                    product=self._clean(r.get("cart_product_name")),
                    qty=self._clean(r.get("cart_qty")),
                    price=self._clean(r.get("cart_price")),
                    file=self._clean(r.get("cart_pic_org")),
                    pic_id=self._clean(r.get("cart_pic_id")),
                )
                for r in cursor.fetchall()
            ]

            try:
                cursor.execute(
                    """
                    SELECT cart_order, cart_product_name, cart_qty, cart_price, cart_pic_org, cart_pic_id
                    FROM ms_cart_archive
                    """
                )
                cart_items.extend(
                    CartItem(
                        order_id=self._clean(r.get("cart_order")),
                        product=self._clean(r.get("cart_product_name")),
                        qty=self._clean(r.get("cart_qty")),
                        price=self._clean(r.get("cart_price")),
                        file=self._clean(r.get("cart_pic_org")),
                        pic_id=self._clean(r.get("cart_pic_id")),
                    )
                    for r in cursor.fetchall()
                )
            except Exception as exc:
                logger.debug("ms_cart_archive query failed: %s", exc)

            status_overrides = {}
            try:
                cursor.execute("SELECT * FROM ms_notes WHERE note_table = 'ms_orders' ORDER BY note_id ASC")
                for record in cursor.fetchall():
                    cleaned = {k: self._clean(v) for k, v in record.items()}
                    override = self._extract_status_override("ms_notes", cleaned)
                    if override:
                        order_id, data = override
                        status_overrides[order_id] = data
            except Exception as exc:
                logger.debug("ms_notes query failed: %s", exc)

            self._apply_status_overrides(orders, status_overrides)

            photo_paths = {}
            for query in [
                "SELECT pic_id, pic_folder, pic_full, pic_pic, pic_large FROM ms_photos",
                "SELECT pic_id, pic_folder, pic_full, pic_pic, pic_large FROM ms_pic",
            ]:
                try:
                    cursor.execute(query)
                    photo_paths = {
                        self._clean(r.get("pic_id")): PhotoPath(
                            folder=self._clean(r.get("pic_folder")),
                            hashed_file=self._clean(r.get("pic_full")),
                            web_file=self._clean(r.get("pic_pic")),
                            large_file=self._clean(r.get("pic_large")),
                        )
                        for r in cursor.fetchall()
                        if self._clean(r.get("pic_id"))
                    }
                    if photo_paths:
                        break
                except Exception as exc:
                    logger.debug("Photo table query failed (%s): %s", query, exc)
                    continue

            return orders, cart_items, photo_paths, status_lookup
        finally:
            conn.close()
