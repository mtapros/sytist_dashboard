from dataclasses import dataclass, field
from typing import Any


@dataclass
class Order:
    id: str
    name: str
    first: str
    last: str
    email: str
    total: str
    date: str = ""
    phone: str = ""
    payment_type: str = ""
    payment_status: str = ""
    payment_transaction: str = ""
    payment_reference: str = ""
    payment_info: str = ""
    status_id: str = ""
    status_name: str = ""
    shipping: str = "0.00"
    tax: str = "0.00"
    fees: str = "0.00"
    discount: str = "0.00"
    subtotal: str = "0.00"
    taxable_amount: str = "0.00"
    tax_percentage: str = "0.0000"
    ship_cost: str = "0.00"
    vat: str = "0.00"
    vat_percentage: str = "0.0000"
    payment_amount: str = "0.00"
    payment_date: str = ""
    credit: str = "0.00"
    gift_certificate: str = "0.00"
    gift_certificate_id: str = ""
    order_fee: str = "0.00"
    order_fee_name: str = ""
    payment_fee: str = "0.00"
    payment_fee_name: str = ""
    shipped_by: str = ""
    shipped_date: str = ""
    shipped_track: str = ""
    shipping_option: str = ""
    address: str = ""
    address_2: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    country: str = ""
    ship_first_name: str = ""
    ship_last_name: str = ""
    ship_address: str = ""
    ship_address_2: str = ""
    ship_city: str = ""
    ship_state: str = ""
    ship_zip: str = ""
    ship_country: str = ""
    customer_notes: str = ""
    admin_notes: str = ""
    short_url: str = ""
    card_last_four: str = ""
    raw_fields: dict[str, Any] = field(default_factory=dict)
    selected: bool = False


@dataclass
class CartItem:
    order_id: str
    product: str
    qty: str
    price: str
    file: str
    pic_id: str


@dataclass
class PhotoPath:
    folder: str
    hashed_file: str
    web_file: str = ""
    large_file: str = ""


@dataclass
class PrintJob:
    source_type: str
    source: str
    display_name: str
    product: str
    size_key: str | None = None
    routed_printer: str | None = None


@dataclass
class ShippingAddress:
    full_name: str = ""
    address_1: str = ""
    address_2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = "US"
    phone: str = ""
    email: str = ""


@dataclass
class PackageDetails:
    weight_oz: str = ""
    length_in: str = ""
    width_in: str = ""
    height_in: str = ""
    mail_class: str = ""


@dataclass
class USPSShipmentMetadata:
    tracking_number: str = ""
    service_name: str = ""
    rate_amount: str = ""
    rate_currency: str = ""
    label_url: str = ""
    label_format: str = ""
    label_created_at: str = ""
    last_error: str = ""
