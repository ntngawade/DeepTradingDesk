"""Narrow Dhan adapter: one reviewed CNC Super Order is the only write operation."""
import json
import math
import ipaddress
from urllib.request import Request
from urllib.error import HTTPError, URLError


class OrderRejected(ValueError):
    definitive_rejection = True


class DhanLiveBroker:
    def __init__(self, session, instruments, opener):
        self.session, self.instruments, self.opener = session, instruments, opener

    def account(self):
        with self.session.lock:
            return self.session.profile.get('dhanClientId') if self.session.token and self.session.profile else None

    def ready(self):
        with self.session.lock:
            if not self.session.token or not self.session.profile or self.session.error:
                raise ValueError('Connect a valid Dhan token before enabling live trading.')
            if self.session.profile.get('ddpi', '').lower() != 'active':
                raise ValueError('Active DDPI authorization is required for this unattended delivery workflow. Check your Dhan profile.')
            return self.session.profile['dhanClientId']

    def call(self, path, payload=None):
        allowed = {'/fundlimit', '/super/orders', '/holdings', '/positions', '/orders', '/trades', '/ip/getIP'}
        if path not in allowed or payload is not None and path != '/super/orders':
            raise ValueError('Unsupported live API request.')
        with self.session.lock:
            if not self.session.token or not self.session.profile:
                raise ValueError('Dhan is disconnected.')
            token, client_id = self.session.token, self.session.profile['dhanClientId']
        request = Request('https://api.dhan.co/v2'+path,
                          headers={'access-token': token, 'client-id': client_id, 'Content-Type': 'application/json'},
                          data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with self.opener().open(request, timeout=10) as response:
                result = json.loads(response.read(4_000_000))
        except HTTPError as error:
            status = error.code
            error.close()
            error_type = OrderRejected if payload is not None and status in (400, 401, 403, 404, 422, 429) else ValueError
            raise error_type(f'Dhan API HTTP {status}. Check token, IP whitelist, DDPI and available funds in Dhan.') from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ValueError('Dhan API response unavailable. If submission was attempted, do not retry manually until reconciled.') from None
        if isinstance(result, dict) and result.get('status') in ('failure', 'error'):
            error_type = OrderRejected if payload is not None else ValueError
            raise error_type('Dhan rejected the request. Inspect the Dhan order book and permissions.')
        return result

    def prerequisites(self):
        self.ready()
        data = self.call('/ip/getIP')
        addresses = [data.get('primaryIP'), data.get('secondaryIP')] if isinstance(data, dict) else []
        configured = False
        for address in addresses:
            try:
                configured |= ipaddress.ip_address(address).is_global
            except (ValueError, TypeError):
                pass
        if not configured:
            raise ValueError('Configure your static public execution IP in Dhan before live trading. This app does not change your IP whitelist.')
        self.funds()

    def instrument(self, symbol):
        return self.instruments.details(symbol)

    def funds(self):
        data = self.call('/fundlimit')
        value = data.get('availabelBalance', data.get('availableBalance')) if isinstance(data, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('Dhan did not return valid available funds.')
        return value

    def check_no_existing_exposure(self, security_id):
        for endpoint, quantity_fields in [('/holdings', ('totalQty',)), ('/positions', ('netQty',))]:
            rows = self.call(endpoint)
            if not isinstance(rows, list):
                raise ValueError('Unable to verify existing Dhan holdings/positions.')
            if any(str(row.get('securityId')) == str(security_id) and row.get('exchangeSegment', 'NSE_EQ') == 'NSE_EQ'
                   and any(float(row.get(key) or 0) != 0 for key in quantity_fields) for row in rows):
                raise ValueError('This stock already has a Dhan holding or position. Separate entry automation is blocked to avoid overlapping exposure.')
        orders = self.call('/orders')
        if not isinstance(orders, list):
            raise ValueError('Unable to verify pending Dhan orders.')
        if any(str(row.get('securityId')) == str(security_id) and row.get('exchangeSegment') == 'NSE_EQ'
               and row.get('orderStatus') in ('PENDING', 'TRANSIT', 'PART_TRADED') for row in orders):
            raise ValueError('A pending Dhan order already exists for this stock.')

    def quote(self, symbol):
        result = self.session.quotes([symbol])
        quote = result['quotes'].get(symbol)
        if not quote:
            raise ValueError('Dhan CMP unavailable for '+symbol+'. Live entries paused.')
        return quote

    def quotes(self, symbols):
        return self.session.quotes(symbols)

    def orders(self):
        return self.call('/super/orders')

    def submit(self, record):
        account = self.ready()
        if account != record['account']:
            raise ValueError('The connected Dhan account changed.')
        payload = {'dhanClientId': account, 'correlationId': record['correlationId'],
                   'transactionType': 'BUY', 'exchangeSegment': 'NSE_EQ', 'productType': 'CNC',
                   'orderType': 'LIMIT', 'securityId': record['securityId'], 'quantity': record['quantity'],
                   'price': record['entry'], 'targetPrice': record['target'], 'stopLossPrice': record['sl'],
                   'trailingJump': 0}
        return self.call('/super/orders', payload)

    def exit_fills(self, record):
        rows = self.call('/trades')
        if not isinstance(rows, list):
            raise ValueError('Cannot verify exit executions in Dhan trade book.')
        ids = {record['orderId']} | {str(leg.get('orderId')) for leg in record.get('brokerLegs', []) if leg.get('orderId')}
        entry_rows = [row for row in rows if str(row.get('orderId')) == str(record['orderId']) and
                      str(row.get('dhanClientId')) == record['account'] and str(row.get('securityId')) == record['securityId'] and
                      row.get('transactionType') == 'BUY' and row.get('productType') == 'CNC' and row.get('exchangeSegment') == 'NSE_EQ']
        matches = [row for row in rows if str(row.get('orderId')) in ids and
                   str(row.get('dhanClientId')) == record['account'] and str(row.get('securityId')) == record['securityId']
                   and row.get('transactionType') == 'SELL' and row.get('productType') == 'CNC' and row.get('exchangeSegment') == 'NSE_EQ']
        seen, qty, value, times = set(), 0, 0.0, []
        for row in matches:
            trade_id = row.get('exchangeTradeId')
            q, price = row.get('tradedQuantity'), row.get('tradedPrice')
            if not trade_id or trade_id in seen or not isinstance(q, int) or isinstance(q, bool) or q <= 0 or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                raise ValueError('Invalid or duplicate broker exit fills. Review in Dhan.')
            seen.add(trade_id)
            stamp = row.get('exchangeTime') or row.get('updateTime') or row.get('createTime')
            if isinstance(stamp, str) and len(stamp) == 19:
                times.append(stamp)
            qty, value = qty+q, value+q*price
        expected = record['filledQty']
        if qty > expected:
            raise ValueError('Exit executions exceed recorded entry fills. Review in Dhan.')
        if not qty or qty != expected:
            return None
        entry_times = [row.get('exchangeTime') or row.get('updateTime') or row.get('createTime') for row in entry_rows]
        entry_times = [stamp for stamp in entry_times if isinstance(stamp, str) and len(stamp) == 19]
        return {'quantity': qty, 'price': value/qty,
                'entryTradeAt': min(entry_times) if entry_times else None,
                'exitTradeAt': max(times) if times else None}
