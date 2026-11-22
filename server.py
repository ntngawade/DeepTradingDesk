"""Local Dhan planner with opt-in, individually reviewed CNC live automation."""
import csv
import io
import json
import hmac
import os
import base64
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
import threading
import argparse
import re
import math
from html import escape as escape_html
from app_version import APP_VERSION
import time
from dhan_data import InstrumentDirectory, validate_symbols, executed_sale
from live_engine import LiveEngine
from live_broker import DhanLiveBroker
from secret_store import SecretStore
import chart_ai
from telegram_share import TelegramShare
from urllib.parse import urlsplit
from datetime import datetime, timezone
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


class ConnectionError(ValueError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def dhan_profile(token):
    request = Request('https://api.dhan.co/v2/profile', headers={
        'access-token': token, 'Accept': 'application/json',
    })
    try:
        with build_opener(NoRedirect).open(request, timeout=10) as response:
            result = json.loads(response.read(100_000))
    except HTTPError as error:
        if error.code in (400, 401, 403):
            raise ConnectionError('Dhan rejected the token. Generate a fresh token and reconnect.') from None
        if error.code == 429:
            raise ConnectionError('Dhan rate limit reached. Wait and retry.', retryable=True) from None
        raise ConnectionError('Dhan is unavailable. Please retry shortly.', retryable=True) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ConnectionError('Cannot reach Dhan securely. Check your internet connection and retry.', retryable=True) from None
    if not isinstance(result, dict) or not result.get('dhanClientId'):
        raise ConnectionError('Dhan did not return a valid account profile. Check your token.')
    return {key: str(result.get(key, '')) for key in
            ('dhanClientId', 'tokenValidity', 'activeSegment', 'ddpi', 'dataPlan', 'dataValidity')}


def dhan_read(token, client_id, path, payload=None):
    """Only these read-only APIs are permitted; POST is used for quote retrieval."""
    if path not in ('/marketfeed/ltp', '/marketfeed/ohlc', '/holdings', '/positions') and not (re.fullmatch(r'/trades/[A-Za-z0-9_-]{1,80}', path) or re.fullmatch(r'/trades/\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}/\d{1,3}', path)):
        raise ConnectionError('Unsupported read-only Dhan API.')
    request = Request('https://api.dhan.co/v2' + path,
                      headers={'access-token': token, 'client-id': client_id,
                               'Accept': 'application/json', 'Content-Type': 'application/json'},
                      data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with build_opener(NoRedirect).open(request, timeout=10) as response:
            result = json.loads(response.read(2_000_000))
    except HTTPError as error:
        code = error.code
        error.close()
        if code == 401:
            if path in ('/marketfeed/ltp', '/marketfeed/ohlc'):
                raise ConnectionError('Dhan denied market-data access (HTTP 401). A connected Trading API account does not grant CMP access. Check Data API subscription and token permissions; portfolio access may still work.') from None
            raise ConnectionError('Dhan rejected the session. Update your access token in Settings.') from None
        if code == 403:
            if path in ('/marketfeed/ltp', '/marketfeed/ohlc'):
                raise ConnectionError('Dhan denied CMP access (HTTP 403). The free Trading API plan does not include market quotes. Check or activate Data API access in Dhan; your trading-account connection can still be valid.') from None
            raise ConnectionError('Dhan denied portfolio access. Check your token and Trading API permissions.') from None
        if code == 429:
            raise ConnectionError('Dhan rate limit reached. Wait before refreshing.') from None
        if path in ('/marketfeed/ltp', '/marketfeed/ohlc'):
            if code in (500, 502, 503, 504):
                raise ConnectionError(f'Dhan market-data service is temporarily unavailable (HTTP {code}). Retry CMP refresh shortly; this is not a sell-order-ID issue.') from None
            raise ConnectionError(f'Dhan market-data request failed (HTTP {code}). Check Data API access and retry CMP refresh.') from None
        raise ConnectionError(f'Dhan request failed (HTTP {code}). Check access or the sell order ID.') from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ConnectionError('Unable to read Dhan data. Check the connection and retry.') from None
    if isinstance(result, dict) and result.get('status') == 'failure':
        raise ConnectionError('Dhan could not return data. Verify API permissions, subscription and instruments.')
    return result


INSTRUMENTS = InstrumentDirectory()


class DhanSession:
    def __init__(self, store=None):
        self.lock = threading.RLock()
        self.token = None
        self.profile = None
        self.checked_at = None
        self.error = None
        self.quote_cache = None
        self.quote_key = None
        self.quote_time = 0
        self.store = store
        self.remembered = False
        self.restoring = False
        self.saved_client_id = None
        self.needs_token = False
        self.portfolio_cache = None
        self.portfolio_time = 0

    def status(self):
        with self.lock:
            return {'connected': bool(self.token and self.profile and not self.error),
                    'hasSession': bool(self.token), 'profile': self.profile,
                    'checkedAt': self.checked_at, 'error': self.error,
                    'remembered': self.remembered, 'restoring': self.restoring,
                    'clientId': (self.profile or {}).get('dhanClientId') or self.saved_client_id,
                    'needsToken': self.needs_token,
                    'executionMode': 'paper', 'backendVersion': APP_VERSION,
                    'features': {'marketData': True, 'executedSaleRead': True, 'liveOrders': True}}

    def connect(self, client_id, token, remember=False):
        if not isinstance(client_id, str) or not client_id.isdigit() or len(client_id) > 20:
            raise ConnectionError('Enter your numeric Dhan client ID.')
        if not isinstance(token, str) or not 20 <= len(token) <= 8192 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ConnectionError('Enter a valid access token without spaces or line breaks.')
        with self.lock:
            profile = dhan_profile(token)
            if profile['dhanClientId'] != client_id:
                raise ConnectionError('The token belongs to a different client ID.')
            if self.store:
                try:
                    if remember:
                        self.store.save(client_id, token)
                    else:
                        self.store.forget()
                except (OSError, ValueError):
                    raise ConnectionError('Could not update secure saved credentials. Uncheck Remember securely to connect without saving, or check Windows account/file permissions.') from None
            self.token, self.profile = token, profile
            self.remembered = bool(remember and self.store)
            self.saved_client_id = client_id if self.remembered else None
            self.needs_token = False
            self.quote_cache = self.quote_key = None
            self.portfolio_cache = None
            self.checked_at = datetime.now(timezone.utc).isoformat()
            self.error = None
            return self.status()

    def restore(self):
        if not self.store:
            return
        self.restoring = True
        saved = None
        try:
            saved = self.store.load()
            if saved:
                with self.lock:
                    self.saved_client_id = saved['clientId']
                    self.remembered = True
                self.connect(saved['clientId'], saved['token'], remember=True)
        except (OSError, ValueError) as error:
            with self.lock:
                if saved and getattr(error, 'retryable', False):
                    self.token, self.profile = saved['token'], {'dhanClientId': saved['clientId']}
                    self.remembered = True
                    self.error = 'Saved token retained. Dhan is temporarily unavailable; connection checks will retry.'
                else:
                    self.needs_token = bool(saved)
                    self.error = 'Saved Dhan connection could not be restored. The token may have expired or been revoked, or the saved file may be inaccessible. Enter a valid token again.'
        finally:
            self.restoring = False

    def refresh(self):
        with self.lock:
            if not self.token:
                return self.status()
            try:
                profile = dhan_profile(self.token)
                if self.profile and profile['dhanClientId'] != self.profile['dhanClientId']:
                    raise ConnectionError('Dhan account identity changed unexpectedly. Reconnect explicitly.')
                self.profile = profile
                self.error = None
                self.needs_token = False
            except ConnectionError as error:
                self.error = str(error)
                self.needs_token = not error.retryable
            self.checked_at = datetime.now(timezone.utc).isoformat()
            return self.status()

    def disconnect(self):
        with self.lock:
            if self.store:
                try:
                    self.store.forget()
                except OSError:
                    self.token = self.profile = None
                    self.error = 'Disconnected in memory, but Windows could not remove the saved credential file. Check file permissions before restarting.'
                    return self.status()
            self.token = self.profile = self.checked_at = self.error = None
            self.remembered = False
            self.saved_client_id = None
            self.needs_token = False
            self.quote_cache = self.quote_key = None
            self.portfolio_cache = None
            return self.status()

    def portfolio(self):
        with self.lock:
            if not self.token or not self.profile or self.error:
                raise ConnectionError('Connect Dhan in Settings to read current holdings and positions.')
            if self.portfolio_cache and time.monotonic() - self.portfolio_time < 25:
                return self.portfolio_cache
            holdings = dhan_read(self.token, self.profile['dhanClientId'], '/holdings')
            positions = dhan_read(self.token, self.profile['dhanClientId'], '/positions')
            if not isinstance(holdings, list) or not isinstance(positions, list):
                raise ConnectionError('Dhan did not return a valid holdings/positions list.')
            def number(value):
                try:
                    value = float(value)
                    return value if math.isfinite(value) else None
                except (ValueError, TypeError):
                    return None
            def item(row, holding):
                return {'symbol': str(row.get('tradingSymbol', ''))[:60], 'securityId': str(row.get('securityId', '')),
                        'quantity': number(row.get('totalQty' if holding else 'netQty')),
                        'averagePrice': number(row.get('avgCostPrice' if holding else 'costPrice')),
                        'availableQty': number(row.get('availableQty')) if holding else None,
                        'unrealizedProfit': number(row.get('unrealizedProfit')) if not holding else None}
            result = {'holdings': [item(r, True) for r in holdings if isinstance(r, dict) and number(r.get('totalQty')) not in (None, 0)],
                      'positions': [item(r, False) for r in positions if isinstance(r, dict) and r.get('productType') == 'CNC' and r.get('exchangeSegment') in ('NSE_EQ', 'BSE_EQ') and number(r.get('netQty')) not in (None, 0)],
                      'checkedAt': datetime.now(timezone.utc).isoformat(), 'clientId': self.profile['dhanClientId']}
            self.portfolio_cache, self.portfolio_time = result, time.monotonic()
            return result

    def quotes(self, symbols):
        symbols = validate_symbols(symbols)
        with self.lock:
            if not self.token or not self.profile or self.error:
                raise ConnectionError('Connect a valid Dhan account in Settings first.')
            if str(self.profile.get('dataPlan', '')).lower() in ('deactive', 'inactive', 'expired'):
                raise ConnectionError('Dhan is connected, but its profile reports an inactive Data API plan. The free Trading API plan supports orders and holdings, not CMP market quotes. Activate Data API access in Dhan to use live CMP.')
            try:
                mapping = INSTRUMENTS.resolve(symbols)
            except Exception:
                raise ConnectionError('Cannot load the Dhan instrument list. Check internet access and retry.') from None
            key = tuple(symbols)
            if self.quote_cache is not None and self.quote_key == key and time.monotonic() - self.quote_time < 0.9:
                return self.quote_cache
            # Dhan market-quote APIs allow one request per second, including other app callers.
            elapsed = time.monotonic() - self.quote_time
            if mapping and self.quote_time and elapsed < 1.05:
                time.sleep(1.05 - elapsed)
            data = dhan_read(self.token, self.profile['dhanClientId'], '/marketfeed/ohlc',
                             {'NSE_EQ': sorted(set(mapping.values()))}) if mapping else {}
            if not isinstance(data, dict) or not isinstance(data.get('data', {}), dict):
                raise ConnectionError('Dhan returned an invalid market-data response.')
            prices = data.get('data', {}).get('NSE_EQ', {})
            if not isinstance(prices, dict):
                raise ConnectionError('Dhan returned an invalid equity quote response.')
            fetched = datetime.now(timezone.utc).isoformat()
            quotes = {}
            for symbol, security_id in mapping.items():
                quote = prices.get(str(security_id), {})
                price = quote.get('last_price') if isinstance(quote, dict) else None
                ohlc = quote.get('ohlc') if isinstance(quote, dict) else None
                previous_close = ohlc.get('close') if isinstance(ohlc, dict) else None
                if isinstance(price, (int, float)) and math.isfinite(price) and price > 0:
                    quotes[symbol] = {'price': price, 'securityId': str(security_id),
                                      'previousClose': previous_close if isinstance(previous_close, (int, float)) and math.isfinite(previous_close) and previous_close > 0 else None,
                                      'dayChange': price - previous_close if isinstance(previous_close, (int, float)) and math.isfinite(previous_close) and previous_close > 0 else None,
                                      'source': 'Dhan', 'fetchedAt': fetched}
            result = {'quotes': quotes, 'unresolved': [s for s in symbols if s not in mapping],
                      'missing': [s for s in mapping if s not in quotes],
                      'quoteDiagnostics': {'apiStatus': str(data.get('status') or 'unknown')[:30],
                                           'requestedInstrumentCount': len(set(mapping.values())),
                                           'returnedInstrumentCount': len(prices),
                                           'returnedSegmentNames': sorted(str(k)[:30] for k in data.get('data', {}))},
                      'fetchedAt': fetched}
            self.quote_cache, self.quote_key, self.quote_time = result, key, time.monotonic()
            return result

    def sale(self, symbol, order_id, expected_qty):
        validate_symbols([symbol])
        if not isinstance(order_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', order_id):
            raise ConnectionError('Enter a valid Dhan SELL order ID.')
        if isinstance(expected_qty, bool) or not isinstance(expected_qty, int) or expected_qty <= 0:
            raise ConnectionError('A recorded executed quantity is required.')
        with self.lock:
            if not self.token or not self.profile or self.error:
                raise ConnectionError('Connect a valid Dhan account first.')
            try:
                mapping = INSTRUMENTS.resolve([symbol])
            except Exception:
                raise ConnectionError('Cannot load the Dhan instrument list. Check internet access and retry.') from None
            if symbol not in mapping:
                raise ConnectionError('Stock not found in the supported NSE equity instrument list.')
            data = dhan_read(self.token, self.profile['dhanClientId'], '/trades/' + order_id)
            return executed_sale(data, order_id, mapping[symbol], self.profile['dhanClientId'], expected_qty)

    def trade_history(self, symbol, from_date, to_date):
        validate_symbols([symbol])
        if not isinstance(from_date, str) or not isinstance(to_date, str):
            raise ValueError('Choose a valid date range for Dhan trade history.')
        try:
            start, end = datetime.strptime(from_date, '%Y-%m-%d').date(), datetime.strptime(to_date, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError('Choose valid start and end dates for Dhan trade history.') from None
        if start > end or (end-start).days > 1825:
            raise ValueError('Choose a date range of up to five years, with the start date before the end date.')
        with self.lock:
            if not self.token or not self.profile or self.error:
                raise ConnectionError('Connect Dhan in Settings to search your past trades.')
            try:
                mapping = INSTRUMENTS.resolve([symbol])
            except Exception:
                raise ConnectionError('Cannot load the Dhan instrument list. Check internet access and retry.') from None
            if symbol not in mapping:
                raise ConnectionError('Stock not found in the supported NSE equity instrument list.')
            client_id, security_id = self.profile['dhanClientId'], str(mapping[symbol])
            grouped = {}
            pages_read = 0
            truncated = True
            for page in range(20):
                data = dhan_read(self.token, client_id, f'/trades/{from_date}/{to_date}/{page}')
                pages_read += 1
                if not isinstance(data, list):
                    raise ConnectionError('Dhan returned an invalid trade-history page.')
                if not data:
                    truncated = False
                    break
                for row in data:
                    if not isinstance(row, dict) or any((str(row.get('dhanClientId')) != client_id,
                            str(row.get('securityId')) != security_id, row.get('transactionType') not in ('BUY','SELL'),
                            row.get('exchangeSegment') != 'NSE_EQ', row.get('productType') != 'CNC')):
                        continue
                    order_id = str(row.get('orderId') or '')
                    try:
                        qty, price = int(row.get('tradedQuantity')), float(row.get('tradedPrice'))
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', order_id) or qty <= 0 or not math.isfinite(price) or price <= 0:
                        continue
                    key = (order_id, row['transactionType'])
                    record = grouped.setdefault(key, {'orderId':order_id, 'side':row['transactionType'], 'quantity':0,
                        'turnover':0.0, 'firstTradeAt':'', 'lastTradeAt':''})
                    record['quantity'] += qty
                    record['turnover'] += qty*price
                    stamp = row.get('exchangeTime') or row.get('updateTime') or row.get('createTime') or ''
                    if isinstance(stamp, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', stamp):
                        if not record['firstTradeAt'] or stamp < record['firstTradeAt']:
                            record['firstTradeAt'] = stamp
                        if not record['lastTradeAt'] or stamp > record['lastTradeAt']:
                            record['lastTradeAt'] = stamp
            orders = []
            for record in grouped.values():
                record['averagePrice'] = round(record.pop('turnover')/record['quantity'], 4)
                orders.append(record)
            orders.sort(key=lambda r:(r['firstTradeAt'],r['orderId'],r['side']))
            return {'symbol':symbol, 'fromDate':from_date, 'toDate':to_date, 'orders':orders,
                    'pagesRead':pages_read, 'truncated':truncated}


DHAN = DhanSession(SecretStore(ROOT / 'runtime' / 'dhan-session.bin'))
TELEGRAM = TelegramShare(ROOT / 'runtime' / 'telegram.bin')
LIVE = None


def live_engine():
    if LIVE is None:
        raise ValueError('Live engine is not running. Restart the updated application backend.')
    return LIVE


def read_xlsx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 40_000_000:
            raise ValueError('Workbook is too large when unpacked.')
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(n.itertext()) for n in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        relationships = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        targets = {r.attrib['Id']: r.attrib['Target'] for r in relationships}
        sheets = {}
        for sheet in workbook.find('m:sheets', NS):
            target = targets[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            rows = []
            for row in ET.fromstring(archive.read(path)).findall('.//m:sheetData/m:row', NS):
                while len(rows) < int(row.attrib.get('r', len(rows) + 1)) - 1:
                    rows.append([])
                values = []
                for cell in row:
                    letters = ''.join(c for c in cell.attrib.get('r', '') if c.isalpha())
                    col = 0
                    for char in letters:
                        col = col * 26 + ord(char.upper()) - 64
                    while len(values) < col:
                        values.append('')
                    value = cell.find('m:v', NS)
                    value = (value.text or '') if value is not None else ''
                    if cell.attrib.get('t') == 's':
                        value = strings[int(value)] if value else ''
                    elif cell.attrib.get('t') == 'inlineStr':
                        value = ''.join(cell.find('m:is', NS).itertext())
                    if col:
                        values[col - 1] = value
                rows.append(values)
            sheets[sheet.attrib['name']] = rows
        return sheets


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        if not self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def local_request(self):
        host = self.headers.get('Host', '')
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        origin = self.headers.get('Origin')
        remote = self.client_address[0]
        if host in allowed and remote in ('127.0.0.1', '::1'):
            return not origin or origin == 'http://' + host
        public_hosts = {item.strip().lower() for item in os.environ.get('DESK_ALLOWED_HOSTS', '').split(',') if item.strip()}
        proxy_token = os.environ.get('DESK_PROXY_TOKEN', '')
        if (not proxy_token or host.lower() not in public_hosts or remote not in ('127.0.0.1', '::1')
                or not hmac.compare_digest(self.headers.get('X-Desk-Proxy-Token', ''), proxy_token)):
            return False
        return not origin or origin == 'https://' + host.lower()

    def json_response(self, data, status=200):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def application_page(self):
        """Include a fresh account snapshot so initial holdings do not depend on a second script request."""
        try:
            snapshot = DHAN.portfolio()
        except (ValueError, OSError):
            snapshot = {'error': 'Dhan account snapshot is not available yet. Check connection or use Sync Dhan holdings.'}
        embedded = json.dumps(snapshot, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        html = html.replace('<script id="initialDhanPortfolio" type="application/json">{}</script>',
                            '<script id="initialDhanPortfolio" type="application/json">' + embedded + '</script>')
        runtime = json.dumps({'backendVersion': APP_VERSION, 'chartReader': chart_ai.status()}, ensure_ascii=True).replace('<', '\\u003c')
        html = html.replace('<script id="initialAppRuntime" type="application/json">{}</script>',
                            '<script id="initialAppRuntime" type="application/json">' + runtime + '</script>')
        holdings, positions = snapshot.get('holdings', []), snapshot.get('positions', [])
        held_symbols = {str(row.get('symbol')) for row in holdings}
        rows = []
        def numeric(value, decimals=2):
            return f'{value:,.{decimals}f}' if isinstance(value, (int, float)) and math.isfinite(value) else '--'
        for kind, records in [('holding', holdings), ('position', positions)]:
            for row in records:
                if kind == 'position' and str(row.get('symbol')) in held_symbols:
                    continue
                symbol = escape_html(str(row.get('symbol', '')))
                average = '&#8377;' + numeric(row.get('averagePrice'))
                rows.append(f'<tr data-holding-symbol="{symbol}"><td><b>{symbol}</b><small class="stock-sub">From Dhan account</small></td><td>--</td><td><button type="button" data-action="refreshPortfolio">Sync Dhan holdings</button></td><td>Dhan</td><td><b>{numeric(row.get("quantity"), 0)}</b></td><td>{average}</td><td>--</td><td>--</td><td>--</td><td>--</td><td>--</td><td>Dhan {kind}</td><td>Verified account position. Strategy details load from your saved plans.</td><td>Not recorded</td></tr>')
        message = snapshot.get('error') or f'{len(rows)} Dhan account positions included with this page. Backend {APP_VERSION}.'
        html = html.replace('<tbody id="ongoingRows"></tbody>', '<tbody id="ongoingRows">' + (''.join(rows) or '<tr><td colspan="14">' + escape_html(message) + '</td></tr>') + '</tbody>')
        html = html.replace('<p id="ongoingSyncStatus" class="hint" role="status">Loading Dhan account holdings...</p>', '<p id="ongoingSyncStatus" class="hint" role="status">' + escape_html(message) + '</p>')
        html = html.replace('<span id="deskBuild">Holdings UI 15</span>', f'<span id="deskBuild">Holdings UI 15 / backend {APP_VERSION}</span>')
        if urlsplit(self.path).path == '/holdings':
            html = html.replace('<section id="dashboard">', '<section id="dashboard" class="hidden">')
            html = html.replace('<section id="live" class="hidden">', '<section id="live">')
            html = html.replace('data-page="dashboard" class="active"', 'data-page="dashboard"')
            html = html.replace('<button data-page="live">', '<button data-page="live" class="active">')
            html = html.replace('id="pageLabel">MARKET RADAR / DELIVERY EQUITIES', 'id="pageLabel">HOLDINGS / LIVE DELIVERY')
        payload = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def do_GET(self):
        if not self.local_request():
            self.send_error(403)
            return
        if urlsplit(self.path).path in ('/', '/index.html', '/holdings'):
            self.application_page()
        elif self.path == '/api/app/status':
            self.json_response({'application': 'DeepTradingDesk', 'backendVersion': APP_VERSION, 'workspace': str(ROOT)})
        elif self.path == '/api/dhan/status':
            self.json_response(DHAN.status())
        elif self.path == '/api/telegram/status':
            try:
                self.json_response(TELEGRAM.status())
            except (ValueError, OSError) as error:
                self.json_response({'configured': False, 'chatId': '', 'tokenSaved': False,
                                    'error': str(error)[:240]})
        elif urlsplit(self.path).path == '/api/chart-ai/status':
            self.json_response(chart_ai.status())
        elif self.path == '/api/dhan/portfolio':
            try:
                self.json_response(DHAN.portfolio())
            except ConnectionError as error:
                self.json_response({'error': str(error)}, 400)
        elif self.path == '/api/live/status':
            try:
                self.json_response(live_engine().status())
            except ValueError as error:
                self.json_response({'error': str(error)}, 503)
        elif self.path.startswith('/api/'):
            self.send_error(404)
        else:
            if self.allowed_asset():
                super().do_GET()
            else:
                self.send_error(404)

    def allowed_asset(self):
        return urlsplit(self.path).path in {'/', '/index.html', '/app.js', '/import-core.js', '/trade-math.js',
                                           '/dhan-connection.js', '/market-data.js', '/live-controls.js', '/portfolio-view.js', '/chart-lab.js', '/tracker-data.json'}

    def do_HEAD(self):
        if self.local_request() and urlsplit(self.path).path in ('/', '/index.html', '/holdings'):
            self.application_page()
        elif self.local_request() and self.allowed_asset():
            super().do_HEAD()
        else:
            self.send_error(404)

    def do_POST(self):
        if not self.local_request():
            self.json_response({'error': 'Only same-origin local requests are allowed.'}, 403)
            return
        if self.path == '/api/chart-ai/analyze':
            try:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    self.json_response({'error': 'JSON body required.'}, 415)
                    return
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 7_100_000:
                    raise ValueError('Chart request is too large. Use an image under 5 MB.')
                self.json_response(chart_ai.analyze(json.loads(self.rfile.read(size))))
            except (ValueError, TypeError) as error:
                self.json_response({'error': str(error)}, 400)
            return
        if self.path in ('/api/telegram/config', '/api/telegram/send', '/api/telegram/find-chat', '/api/telegram/check'):
            try:
                if self.path == '/api/telegram/find-chat':
                    self.json_response(TELEGRAM.find_chats())
                    return
                if self.path == '/api/telegram/check':
                    self.json_response(TELEGRAM.check())
                    return
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    self.json_response({'error': 'JSON body required.'}, 415)
                    return
                size = int(self.headers.get('Content-Length', 0))
                maximum = 7_100_000 if self.path.endswith('/send') else 20_000
                if not 0 < size <= maximum:
                    raise ValueError('Telegram request is too large.')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('Invalid Telegram request.')
                if self.path.endswith('/config'):
                    result = TELEGRAM.configure(body.get('botToken'), body.get('chatId'))
                else:
                    encoded = body.get('image')
                    if not isinstance(encoded, str) or len(encoded) > 6_700_000:
                        raise ValueError('Attach a chart screenshot under 5 MB.')
                    image = base64.b64decode(encoded, validate=True)
                    result = TELEGRAM.send_photo(body.get('caption'), image, body.get('filename', 'chart.png'))
                self.json_response(result)
            except (ValueError, TypeError) as error:
                self.json_response({'error': str(error)}, 400)
            return
        if self.path.startswith('/api/live/'):
            try:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    self.json_response({'error': 'JSON body required.'}, 415)
                    return
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 20_000:
                    raise ValueError('Invalid live request size.')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('Invalid live request.')
                engine = live_engine()
                if self.path == '/api/live/enable':
                    if body.get('acknowledgeRealOrders') is not True:
                        raise ValueError('Explicit live-order acknowledgement is required.')
                    result = engine.enable()
                elif self.path == '/api/live/pause':
                    engine.pause(manual=True)
                    result = engine.status()
                elif self.path == '/api/live/preview':
                    result = engine.preview(body.get('plan'), body.get('settings'), body.get('triggerPercent', 1))
                elif self.path == '/api/live/arm':
                    result = engine.arm(body.get('previewId'))
                elif self.path == '/api/live/disarm':
                    result = engine.disarm(body.get('tradeId'))
                elif self.path == '/api/live/plan-enabled':
                    result = engine.set_plan_enabled(body.get('tradeId'), body.get('enabled'))
                else:
                    self.json_response({'error': 'Unknown live endpoint.'}, 404)
                    return
                self.json_response(result)
            except (ValueError, TypeError) as error:
                self.json_response({'error': str(error)}, 400)
            return
        if self.path.startswith('/api/dhan/'):
            try:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    self.json_response({'error': 'JSON body required.'}, 415)
                    return
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 12_000:
                    raise ConnectionError('Invalid connection request size.')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ConnectionError('Invalid connection request.')
                if self.path == '/api/dhan/connect':
                    result = DHAN.connect(body.get('clientId'), body.get('accessToken'), remember=body.get('remember') is True)
                    if LIVE:
                        LIVE.pause()
                elif self.path == '/api/dhan/refresh':
                    result = DHAN.refresh()
                elif self.path == '/api/dhan/disconnect':
                    if LIVE:
                        LIVE.pause()
                    result = DHAN.disconnect()
                elif self.path == '/api/dhan/quotes':
                    result = DHAN.quotes(body.get('symbols'))
                elif self.path == '/api/dhan/sale':
                    result = DHAN.sale(body.get('symbol'), body.get('orderId'), body.get('quantity'))
                elif self.path == '/api/dhan/trade-history':
                    result = DHAN.trade_history(body.get('symbol'), body.get('fromDate'), body.get('toDate'))
                else:
                    self.json_response({'error': 'Unknown connection endpoint.'}, 404)
                    return
                self.json_response(result)
            except ConnectionError as error:
                self.json_response({'error': str(error)}, 400)
            except (ValueError, TypeError) as error:
                message = str(error) if self.path in ('/api/dhan/quotes', '/api/dhan/sale', '/api/dhan/trade-history') else 'Invalid connection details or request.'
                self.json_response({'error': message}, 400)
            return
        if self.path != '/api/import':
            self.send_error(404)
            return
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 5_000_000:
                raise ValueError('Choose a file smaller than 5 MB.')
            data = self.rfile.read(size)
            sheets = read_xlsx(data) if data.startswith(b'PK') else {'CSV': list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))}
            payload = json.dumps({'sheets': sheets}).encode()
            self.send_response(200)
        except Exception as error:
            payload = json.dumps({'error': str(error)}).encode()
            self.send_response(400)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Deep Trading Desk application backend')
    parser.add_argument('--port', type=int, default=8081)
    parser.add_argument('--host', default=os.environ.get('DESK_BIND_HOST', '127.0.0.1'))
    args = parser.parse_args()
    try:
        http = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError:
        parser.exit(1, f'Port {args.port} is unavailable. Use --port 8081 or stop the old server.\n')
    print(f'Deep Trading Desk (Dhan backend enabled): http://{args.host}:{http.server_port}', flush=True)
    LIVE = LiveEngine(ROOT / 'runtime' / 'live.sqlite', DhanLiveBroker(DHAN, INSTRUMENTS, lambda: build_opener(NoRedirect)))
    LIVE.start()
    threading.Thread(target=DHAN.restore, daemon=True, name='restore-dhan-connection').start()
    http.serve_forever()
