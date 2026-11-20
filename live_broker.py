"""Narrow Dhan adapter: one reviewed CNC Super Order is the only write operation."""
import json
import math
import ipaddress
import re
from datetime import date, datetime, timedelta
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
        historical_trades = bool(re.fullmatch(r'/trades/\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}/\d{1,3}', path))
        if path not in allowed and not historical_trades or payload is not None and path != '/super/orders':
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

    def entry_fills(self, record):
        """Return a verified weighted BUY fill from Dhan's trade book for one order."""
        rows = self.call('/trades')
        if not isinstance(rows, list):
            raise ValueError('Cannot verify entry executions in Dhan trade book.')
        matches = [row for row in rows if str(row.get('orderId')) == str(record['orderId']) and
                   str(row.get('dhanClientId')) == record['account'] and str(row.get('securityId')) == record['securityId'] and
                   row.get('transactionType') == 'BUY' and row.get('productType') == 'CNC' and row.get('exchangeSegment') == 'NSE_EQ']
        seen, quantity, value, times = set(), 0, 0.0, []
        for row in matches:
            trade_id = row.get('exchangeTradeId')
            qty, price = row.get('tradedQuantity'), row.get('tradedPrice')
            if (not trade_id or trade_id in seen or not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0
                    or not isinstance(price, (int, float)) or isinstance(price, bool) or not math.isfinite(price) or price <= 0):
                raise ValueError('Dhan trade book returned invalid or duplicate entry fills. Review in Dhan.')
            seen.add(trade_id)
            stamp = row.get('exchangeTime') or row.get('updateTime') or row.get('createTime')
            if isinstance(stamp, str) and len(stamp) == 19:
                times.append(stamp)
            quantity, value = quantity+qty, value+qty*price
        if quantity != record['filledQty'] or quantity > record['quantity']:
            return None
        return {'quantity': quantity, 'price': value/quantity, 'tradeAt': min(times) if times else None}

    def submit(self, record):
        account = self.ready()
        if account != record['account']:
            raise ValueError('The connected Dhan account changed.')
        payload = {'dhanClientId': account, 'correlationId': record['correlationId'],
                   'transactionType': 'BUY', 'exchangeSegment': 'NSE_EQ', 'productType': 'CNC',
                   'orderType': 'MARKET', 'securityId': record['securityId'], 'quantity': record['quantity'],
                   'price': 0, 'targetPrice': record['target'], 'stopLossPrice': record['sl'],
                   'trailingJump': 0}
        return self.call('/super/orders', payload)

    def exit_fills(self, record):
        rows = self.call('/trades')
        result = self._exit_fills_from_rows(record, rows)
        if result:
            return result
        # Dhan's Super Order list and trade book are day-scoped. For older CNC
        # orders, reconcile from the historical trade endpoint before treating
        # a completed sale as unresolved.
        anchor = record.get('submittedAt') or record.get('entryTradeAt') or record.get('reviewedAt')
        try:
            start = datetime.fromisoformat(str(anchor).replace('Z', '+00:00')).date()
        except (TypeError, ValueError):
            return None
        end = date.today()
        if start > end or (end - start).days > 90:
            return None
        history = []
        for page in range(20):
            batch = self.call(f'/trades/{start.isoformat()}/{end.isoformat()}/{page}')
            if not isinstance(batch, list):
                raise ValueError('Dhan historical trade response is invalid; keep the trade unresolved.')
            history.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise ValueError('Dhan historical trade history is too large to verify safely; keep the trade unresolved.')
        return self._exit_fills_from_rows(record, history)

    def _exit_fills_from_rows(self, record, rows):
        if not isinstance(rows, list):
            raise ValueError('Cannot verify exit executions in Dhan trade book.')
        ids = {record['orderId']} | {str(leg.get('orderId')) for leg in record.get('brokerLegs', []) if leg.get('orderId')}
        entry_rows = [row for row in rows if str(row.get('orderId')) == str(record['orderId']) and
                      str(row.get('dhanClientId')) == record['account'] and str(row.get('securityId')) == record['securityId'] and
                      row.get('transactionType') == 'BUY' and row.get('productType') == 'CNC' and row.get('exchangeSegment') == 'NSE_EQ']
        entry_ids, entry_qty = set(), 0
        for row in entry_rows:
            trade_id, q, traded_price = row.get('exchangeTradeId'), row.get('tradedQuantity'), row.get('tradedPrice')
            if (not trade_id or trade_id in entry_ids or not isinstance(q, int) or isinstance(q, bool) or q <= 0
                    or not isinstance(traded_price, (int, float)) or isinstance(traded_price, bool) or not math.isfinite(traded_price) or traded_price <= 0):
                raise ValueError('Invalid or duplicate broker entry fills. Review in Dhan.')
            entry_ids.add(trade_id)
            entry_qty += q
        expected = record['filledQty']
        if entry_qty != expected:
            return None
        entry_stamps = [row.get('exchangeTime') or row.get('updateTime') or row.get('createTime') for row in entry_rows]
        entry_stamps = [stamp for stamp in entry_stamps if isinstance(stamp, str) and len(stamp) == 19]
        try:
            entry_time = min(datetime.fromisoformat(stamp.replace('Z', '+00:00')).replace(tzinfo=None) for stamp in entry_stamps)
        except ValueError:
            entry_time = None
        all_sells = [row for row in rows if str(row.get('dhanClientId')) == record['account'] and
                     str(row.get('securityId')) == record['securityId'] and row.get('transactionType') == 'SELL' and
                     row.get('productType') == 'CNC' and row.get('exchangeSegment') == 'NSE_EQ']
        matches = [row for row in all_sells if str(row.get('orderId')) in ids]
        linked_ids = {str(row.get('exchangeTradeId')) for row in matches}
        # Some Dhan exits appear in historical trades under the exchange leg's
        # order ID instead of the parent Super Order ID. Accept those only if
        # a full quantity was sold after the verified entry at the plan's SL or
        # target price; this avoids closing on unrelated same-stock sales.
        linked_qty = sum(int(row.get('tradedQuantity') or 0) for row in matches)
        if linked_qty < expected and entry_time is not None:
            tick = float(record.get('tickSize') or 0.05)
            for row in all_sells:
                if str(row.get('orderId')) in ids or str(row.get('exchangeTradeId')) in linked_ids:
                    continue
                stamp = row.get('exchangeTime') or row.get('updateTime') or row.get('createTime')
                try:
                    sold_at = datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).replace(tzinfo=None)
                except ValueError:
                    continue
                quantity, traded_price = row.get('tradedQuantity'), row.get('tradedPrice')
                if (sold_at <= entry_time or not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0
                        or not isinstance(traded_price, (int, float)) or isinstance(traded_price, bool) or not math.isfinite(traded_price)
                        or min(abs(traded_price-record['sl']), abs(traded_price-record['target'])) > tick/2+1e-9):
                    continue
                matches.append(row)
                linked_ids.add(str(row.get('exchangeTradeId')))
                linked_qty += quantity
                if linked_qty >= expected:
                    break
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
        if qty > expected:
            raise ValueError('Exit executions exceed recorded entry fills. Review in Dhan.')
        if not qty or qty != expected:
            return None
        return {'quantity': qty, 'price': value/qty,
                'entryTradeAt': min(entry_stamps) if entry_stamps else None,
                'exitTradeAt': max(times) if times else None}
