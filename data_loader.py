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
    ORDER_STATUS_QUERY = "SELECT status_id, status_name, status_descr, status_show_order FROM ms_order_status"

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
            if char == '\\':
                current_val += char
                escaped = True
                continue
            if char == "'" and not escaped:
                in_quote = not in_quote
                continue
            if not in_quote:
                if char == '(':
                    if not in_tuple:
                        in_tuple = True
                        current_tuple = []
                        current_val = ""
                        continue
                elif char == ')':
                    if in_tuple:
                        current_tuple.append(current_val.strip())
                        results.append(current_tuple)
                        in_tuple = False
                        continue
                elif char == ',':
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
            if line.startswith('`'):
                columns.append(line.split('`')[1])
        return columns

    @staticmethod
    def _extract_create_table_name(line: str):
        marker = 'CREATE TABLE `'
        if not line.startswith(marker):
            return None
        rest = line[len(marker):]
        return rest.split('`', 1)[0]

    @contextmanager
    def _open_sql_stream(self, filepath):
        if os.path.splitext(filepath)[1].lower() != ".zip":
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as stream:
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
                with TextIOWrapper(raw_stream, encoding='utf-8', errors='ignore') as stream:
                    yield stream

    def _build_order(self, record, status_lookup):
        status_id = self._clean(record.get("order_status", ""))
        status_name = status_lookup.get(status_id, {}).get("status_name", "")
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
            for row in cursor.fetchall():
                status_id = self._clean(row[0] if not isinstance(row, dict) else row.get("status_id"))
                lookup[status_id] = {
                    "status_name": self._clean(row[1] if not isinstance(row, dict) else row.get("status_name")),
                    "status_descr": self._clean(row[2] if not isinstance(row, dict) else row.get("status_descr")),
                    "status_show_order": self._clean(row[3] if not isinstance(row, dict) else row.get("status_show_order")),
                }
        except Exception as exc:
            logger.warning("Could not load order status lookup from DB: %s", exc)
        self.order_status_lookup = lookup
        return lookup

    def load_sql_dump(self, filepath):
        orders = []
        cart_items = []
        photo_paths = {}
        status_lookup = {}
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
                    if line.strip().startswith(') ENGINE='):
                        create_columns[create_table_name] = self._columns_from_create("".join(create_buffer))
                        create_buffer = None
                        create_table_name = None
                    continue

                maybe_create = self._extract_create_table_name(line)
                if maybe_create:
                    create_table_name = maybe_create
                    create_buffer = [line]
                    if line.strip().startswith(') ENGINE='):
                        create_columns[create_table_name] = self._columns_from_create("".join(create_buffer))
                        create_buffer = None
                        create_table_name = None
                    continue

                if active_table is None:
                    if line.startswith('INSERT INTO `ms_orders`'):
                        active_table = 'ms_orders'
                        buffer = line[line.find(" VALUES ") + 8:]
                    elif line.startswith('INSERT INTO `ms_cart`'):
                        active_table = 'ms_cart'
                        buffer = line[line.find(" VALUES ") + 8:]
                    elif line.startswith('INSERT INTO `ms_photos`') or line.startswith('INSERT INTO `ms_pic`'):
                        active_table = 'ms_photos'
                        buffer = line[line.find(" VALUES ") + 8:]
                    elif line.startswith('INSERT INTO `ms_order_status`'):
                        active_table = 'ms_order_status'
                        buffer = line[line.find(" VALUES ") + 8:]
                    else:
                        continue
                    in_quote = False
                    escaped = False
                    seed_text = buffer
                else:
                    buffer += line
                    seed_text = line

                for char in seed_text:
                    if escaped:
                        escaped = False
                    elif char == '\\':
                        escaped = True
                    elif char == "'":
                        in_quote = not in_quote

                if active_table and not in_quote and line.strip().endswith(';'):
                    parsed_tuples = self.robust_parse(buffer)
                    cols = create_columns.get(active_table, [])

                    if active_table == 'ms_orders':
                        for parts in parsed_tuples:
                            record = {cols[i]: self._clean(parts[i]) for i in range(min(len(cols), len(parts)))}
                            if record:
                                orders.append(self._build_order(record, status_lookup))

                    elif active_table == 'ms_cart':
                        for parts in parsed_tuples:
                            record = {cols[i]: self._clean(parts[i]) for i in range(min(len(cols), len(parts)))}
                            cart_items.append(CartItem(
                                order_id=self._clean(record.get("cart_order", record.get("order_id", ""))),
                                product=self._clean(record.get("cart_product_name", record.get("cart_product", ""))),
                                qty=self._clean(record.get("cart_qty", "0")),
                                price=self._clean(record.get("cart_price", "0.00")),
                                file=self._clean(record.get("cart_pic_org", record.get("cart_file", ""))),
                                pic_id=self._clean(record.get("cart_pic_id", "")),
                            ))

                    elif active_table == 'ms_photos':
                        for parts in parsed_tuples:
                            record = {cols[i]: self._clean(parts[i]) for i in range(min(len(cols), len(parts)))}
                            pic_id = self._clean(record.get("pic_id", ""))
                            if pic_id:
                                photo_paths[pic_id] = PhotoPath(
                                    folder=self._clean(record.get("pic_folder", "")),
                                    hashed_file=self._clean(record.get("pic_full", "")),
                                )

                    elif active_table == 'ms_order_status':
                        for parts in parsed_tuples:
                            record = {cols[i]: self._clean(parts[i]) for i in range(min(len(cols), len(parts)))}
                            status_id = self._clean(record.get("status_id", ""))
                            if status_id:
                                status_lookup[status_id] = {
                                    "status_name": self._clean(record.get("status_name", "")),
                                    "status_descr": self._clean(record.get("status_descr", "")),
                                    "status_show_order": self._clean(record.get("status_show_order", "")),
                                }

                    active_table = None
                    buffer = ""

        self.order_status_lookup = status_lookup
        if status_lookup:
            for order in orders:
                if not order.status_name:
                    order.status_name = status_lookup.get(order.status_id, {}).get("status_name", "")
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

            photo_paths = {}
            photo_queries = [
                "SELECT pic_id, pic_folder, pic_full FROM ms_photos",
                "SELECT pic_id, pic_folder, pic_full FROM ms_pic",
            ]
            for query in photo_queries:
                try:
                    cursor.execute(query)
                    photo_paths = {
                        self._clean(r.get("pic_id")): PhotoPath(
                            folder=self._clean(r.get("pic_folder")),
                            hashed_file=self._clean(r.get("pic_full")),
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
