"""Read-only instrument resolution and executed sell-fill validation."""
import csv
import io
import re
import threading
import time
from urllib.request import build_opener, Request
from decimal import Decimal, InvalidOperation


class InstrumentDirectory:
    def __init__(self):
        self.lock = threading.RLock()
        self.symbols = {}
        self.indices = {}
        self.loaded = 0
        self.ticks = {}

    @staticmethod
    def parse(rows):
        symbols = {}
        for row in rows:
            if (row.get('SEM_EXM_EXCH_ID') == 'NSE'
                    and row.get('SEM_INSTRUMENT_NAME') == 'EQUITY'
                    and row.get('SEM_SERIES') == 'EQ'):
                symbol = row.get('SEM_TRADING_SYMBOL', '').strip().upper()
                security_id = row.get('SEM_SMST_SECURITY_ID', '')
                if symbol and security_id.isdigit():
                    symbols.setdefault(symbol, set()).add(int(security_id))
        # An ambiguous symbol is never silently assigned an instrument.
        return {symbol: next(iter(ids)) for symbol, ids in symbols.items() if len(ids) == 1}

    @staticmethod
    def parse_indices(rows):
        aliases = {'NIFTYFINANCIALSERVICES': 'NIFTYFINSERVICE',
                   'NIFTYFINANCIALSERVICESINDEX': 'NIFTYFINSERVICE',
                   'NIFTYOILGAS': 'NIFTYOILANDGAS',
                   'NIFTYPVTBANK': 'NIFTYPRIVATEBANK',
                   'NIFTYCONSUMERDURABLES': 'NIFTYCONSRDURBL',
                   'NIFTYMID50': 'NIFTYMIDCAP50', 'NIFTYM50': 'NIFTYMIDCAP50',
                   'GIFTNIFTY': 'NIFTYGIFTY', 'NIFTYGIFTNIFTY': 'NIFTYGIFTY'}
        found = {}
        for row in rows:
            if row.get('SEM_INSTRUMENT_NAME', '').upper() != 'INDEX':
                continue
            if row.get('SEM_EXM_EXCH_ID', '').upper() not in ('NSE', 'BSE', 'IDX_I'):
                continue
            security_id = row.get('SEM_SMST_SECURITY_ID', '')
            raw_names = (row.get('SEM_TRADING_SYMBOL', ''), row.get('SM_SYMBOL_NAME', ''),
                         row.get('SEM_CUSTOM_SYMBOL', ''))
            for raw in raw_names:
                key = re.sub(r'[^A-Z0-9]', '', raw.upper().strip())
                key = aliases.get(key, key)
                if (key.startswith('NIFTY') or key == 'SENSEX') and security_id.isdigit():
                    found.setdefault(key, set()).add(int(security_id))
        return {symbol: next(iter(ids)) for symbol, ids in found.items() if len(ids) == 1}

    def resolve(self, symbols):
        with self.lock:
            if time.monotonic() - self.loaded > 86400 or not self.symbols:
                request = Request('https://images.dhan.co/api-data/api-scrip-master.csv')
                with build_opener().open(request, timeout=20) as response:
                    raw = response.read(60_000_001)
                if len(raw) > 60_000_000:
                    raise ValueError('Dhan instrument list is larger than expected.')
                rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
                equities = [row for row in rows if row.get('SEM_EXM_EXCH_ID') == 'NSE' and row.get('SEM_INSTRUMENT_NAME') == 'EQUITY' and row.get('SEM_SERIES') == 'EQ']
                self.symbols = self.parse(equities)
                self.indices = self.parse_indices(rows)
                # Dhan's compact scrip master reports tick size in paise.
                # API order prices and the rest of the application use rupees.
                self.ticks = {row['SEM_TRADING_SYMBOL'].strip().upper(): float(Decimal(row.get('SEM_TICK_SIZE') or '0') / 100) for row in equities}
                if not self.symbols:
                    raise ValueError('Dhan instrument list has no supported NSE equities.')
                self.loaded = time.monotonic()
            return {symbol: self.symbols[symbol] for symbol in symbols if symbol in self.symbols}

    def resolve_indices(self, symbols):
        requested = validate_indices(symbols)
        with self.lock:
            if time.monotonic() - self.loaded > 86400 or not self.indices:
                # resolve() refreshes the shared master and loads equity and index IDs together.
                self.resolve([])
            return {symbol: self.indices[symbol] for symbol in requested if symbol in self.indices}

    def details(self, symbol):
        mapping = self.resolve([symbol])
        if symbol not in mapping or self.ticks.get(symbol, 0) <= 0:
            raise ValueError('Cannot resolve a unique NSE EQ instrument and tick size for this stock.')
        return {'securityId': str(mapping[symbol]), 'tickSize': self.ticks[symbol]}


def validate_symbols(symbols):
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 100:
        raise ValueError('Request 1 to 100 stock symbols at once.')
    if any(not isinstance(s, str) or not re.fullmatch(r'[A-Z0-9&._ -]{1,30}', s) for s in symbols):
        raise ValueError('Invalid stock symbol.')
    return sorted(set(symbols))


def validate_indices(symbols):
    if not isinstance(symbols, list) or len(symbols) > 100:
        raise ValueError('Request up to 100 sector index symbols at once.')
    if any(not isinstance(s, str) or not re.fullmatch(r'[A-Z0-9&._ -]{1,40}', s) for s in symbols):
        raise ValueError('Invalid sector index symbol.')
    return sorted(set(re.sub(r'[^A-Z0-9]', '', s.upper()) for s in symbols))


def executed_sale(data, order_id, security_id, client_id, expected_qty):
    rows = data if isinstance(data, list) else [data]
    if not rows:
        raise ValueError('No executed fills found for this order.')
    quantity, turnover = 0, Decimal(0)
    seen = set()
    times = []
    for row in rows:
        if not isinstance(row, dict) or any((
            str(row.get('orderId')) != order_id,
            str(row.get('dhanClientId')) != client_id,
            str(row.get('securityId')) != str(security_id),
            row.get('transactionType') != 'SELL',
            row.get('exchangeSegment') != 'NSE_EQ',
            row.get('productType') != 'CNC',
        )):
            raise ValueError('Order must be a delivery SELL for this stock and connected account.')
        trade_id = row.get('exchangeTradeId')
        if not trade_id or trade_id in seen:
            raise ValueError('Missing or duplicate exchange trade IDs; exit was not applied.')
        seen.add(trade_id)
        stamp = row.get('exchangeTime') or row.get('updateTime') or row.get('createTime')
        if isinstance(stamp, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', stamp):
            times.append(stamp)
        try:
            qty = Decimal(str(row.get('tradedQuantity', 0)))
            price = Decimal(str(row.get('tradedPrice', 0)))
        except InvalidOperation:
            raise ValueError('Invalid executed quantity or price received from Dhan.') from None
        if not qty.is_finite() or qty <= 0 or qty != qty.to_integral_value() or not price.is_finite() or price <= 0:
            raise ValueError('Invalid executed fill received from Dhan.')
        quantity += int(qty)
        turnover += qty * price
    if quantity != expected_qty:
        raise ValueError(f'Executed quantity {quantity} does not match recorded quantity {expected_qty}. Partial exits require separate journal records.')
    return {'orderId': order_id, 'quantity': quantity,
            'salePrice': float(turnover / quantity), 'source': 'Dhan execution',
            'firstTradeAt': min(times) if times else None, 'lastTradeAt': max(times) if times else None}
