import json
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError


class ZohoBooksError(Exception):
    pass


class ZohoBooksClient:
    def __init__(self, accounts_domain, client_id, client_secret, refresh_token, organization_id, api_domain=None, timeout=30):
        self.accounts_domain = (accounts_domain or 'https://accounts.zoho.com').rstrip('/')
        self.client_id = client_id or ''
        self.client_secret = client_secret or ''
        self.refresh_token = refresh_token or ''
        self.organization_id = str(organization_id or '').strip()
        self.api_domain = (api_domain or 'https://www.zohoapis.com').rstrip('/')
        self.timeout = timeout
        self._access_token = ''

    def _post_form(self, url, data):
        payload = urlencode(data).encode('utf-8')
        req = Request(url, data=payload, method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            raise ZohoBooksError(body or str(exc)) from exc

    def _request_json(self, method, path, params=None, payload=None):
        if not self._access_token:
            self.refresh_access_token()
        query = dict(params or {})
        query['organization_id'] = self.organization_id
        url = self.api_domain + path
        if query:
            url += '?' + urlencode(query)
        data = None
        req = Request(url, method=method.upper())
        req.add_header('Authorization', f'Zoho-oauthtoken {self._access_token}')
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            req.data = data
            req.add_header('Content-Type', 'application/json')
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode('utf-8', errors='replace')
        except HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            raise ZohoBooksError(body or str(exc)) from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ZohoBooksError(f'Unexpected Zoho response: {body}') from exc

    def refresh_access_token(self):
        token_url = self.accounts_domain + '/oauth/v2/token'
        raw = self._post_form(token_url, {
            'refresh_token': self.refresh_token,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'refresh_token',
        })
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ZohoBooksError(f'Unexpected token response: {raw}') from exc
        token = data.get('access_token')
        if not token:
            raise ZohoBooksError(raw)
        self._access_token = token
        if data.get('api_domain'):
            self.api_domain = data['api_domain'].rstrip('/')
        return data

    def list_contacts(self, email):
        return self._request_json('GET', '/books/v3/contacts', params={'email_contains': email or ''})

    def create_contact(self, order):
        payload = {
            'contact_name': (order.name or f'Order {order.id}').strip(),
            'company_name': '',
            'contact_type': 'customer',
            'billing_address': {
                'attention': order.name or '',
                'address': order.address or '',
                'street2': order.address_2 or '',
                'city': order.city or '',
                'state': order.state or '',
                'zip': order.zip_code or '',
                'country': order.country or '',
            },
            'shipping_address': {
                'attention': (f"{order.ship_first_name or ''} {order.ship_last_name or ''}").strip() or order.name or '',
                'address': order.ship_address or '',
                'street2': order.ship_address_2 or '',
                'city': order.ship_city or '',
                'state': order.ship_state or '',
                'zip': order.ship_zip or '',
                'country': order.ship_country or '',
            },
            'contact_persons': [{
                'first_name': (order.name or '').strip()[:100],
                'email': order.email or '',
                'phone': order.phone or '',
                'is_primary_contact': True,
            }],
        }
        data = self._request_json('POST', '/books/v3/contacts', payload=payload)
        return data.get('contact') or {}

    def find_or_create_contact(self, order):
        email = (order.email or '').strip()
        if email:
            data = self.list_contacts(email)
            for contact in data.get('contacts', []) or []:
                if (contact.get('email') or '').strip().lower() == email.lower():
                    return contact
        return self.create_contact(order)

    def get_invoice_by_number(self, invoice_number):
        data = self._request_json('GET', '/books/v3/invoices', params={'invoice_number_contains': invoice_number})
        for invoice in data.get('invoices', []) or []:
            if str(invoice.get('invoice_number', '')).strip() == str(invoice_number).strip():
                return invoice
        return None

    def create_invoice(self, payload):
        return self._request_json('POST', '/books/v3/invoices', payload=payload)

    @staticmethod
    def build_invoice_number(prefix, order_id):
        return f"{(prefix or '').strip()}{str(order_id).strip()}"
