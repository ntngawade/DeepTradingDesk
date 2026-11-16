"""Explicit CLI for broker-held CNC sell OCO exits; never buys or retries writes.

Run without --apply to inspect a proposal. Each actual submission is recorded
before transmission, so a timeout or process restart cannot silently resend it.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import ipaddress
import json
from pathlib import Path
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener


class HoldingExit:
    def __init__(self, call, instruments, ledger):
        self.call, self.instruments = call, instruments
        self.ledger = Path(ledger)

    def proposal(self, account, symbol, quantity, sl, target):
        if not isinstance(symbol, str) or not symbol or symbol != symbol.upper():
            raise ValueError('Use an uppercase NSE equity symbol.')
        if type(quantity) is not int or quantity < 1:
            raise ValueError('Quantity must be a positive whole number.')
        sl, target = Decimal(str(sl)), Decimal(str(target))
        if not sl.is_finite() or not target.is_finite() or not 0 < sl < target:
            raise ValueError('Positive SL below target is required.')
        profile = self.call('/profile')
        if str(profile.get('dhanClientId')) != account or str(profile.get('ddpi', '')).lower() != 'active':
            raise ValueError('Verify the connected account and active DDPI before placing delivery exits.')
        whitelist = self.call('/ip/getIP')
        addresses = [whitelist.get('primaryIP'), whitelist.get('secondaryIP')]
        valid_ip = False
        for address in addresses:
            try:
                valid_ip |= ipaddress.ip_address(address).is_global
            except (ValueError, TypeError):
                pass
        if not valid_ip:
            raise ValueError('Configure the execution IP whitelist in Dhan first.')
        instrument = self.instruments.details(symbol)
        security = str(instrument['securityId'])
        tick = Decimal(str(instrument['tickSize']))
        if tick <= 0 or sl % tick or target % tick:
            raise ValueError('The exact requested prices do not match the exchange tick size; no rounding or submission performed.')
        holdings = self.call('/holdings')
        if not isinstance(holdings, list):
            raise ValueError('Cannot verify demat holdings.')
        matching = [r for r in holdings if str(r.get('securityId')) == security and r.get('exchange', 'NSE') in ('NSE', 'ALL')]
        if len(matching) != 1:
            raise ValueError('A unique NSE holding is required.')
        holding = matching[0]
        if quantity > float(holding.get('totalQty') or 0) or quantity > float(holding.get('availableQty') or 0):
            raise ValueError('Requested sell quantity exceeds available demat shares.')
        for path in ('/orders', '/super/orders', '/forever/orders'):
            rows = self.call(path)
            if not isinstance(rows, list):
                raise ValueError('Cannot verify existing broker orders; no submission performed.')
            terminal = {'CANCELLED', 'REJECTED', 'EXPIRED', 'CLOSED'}
            if path == '/orders':
                terminal.add('TRADED')
            for row in rows:
                if str(row.get('securityId')) != security or row.get('exchangeSegment', 'NSE_EQ') != 'NSE_EQ':
                    continue
                if path == '/orders' and row.get('transactionType') != 'SELL':
                    continue
                if str(row.get('orderStatus', '')).upper() not in terminal:
                    raise ValueError('An existing order may already control this holding. Review it in Dhan; no duplicate exit submitted.')
        quotes = self.call('/marketfeed/ltp', {'NSE_EQ': [int(security)]})
        quote = quotes.get('data', {}).get('NSE_EQ', {}).get(security, {})
        cmp = Decimal(str(quote.get('last_price', 0)))
        if not cmp.is_finite() or not sl < cmp < target:
            raise ValueError('Dhan LTP must be strictly between SL and target before placing these triggers.')
        identity = account + ':' + security
        correlation = 'HOLD-' + hashlib.sha256(f'{identity}:{quantity}:{sl}:{target}'.encode()).hexdigest()[:24]
        payload = {'dhanClientId': account, 'correlationId': correlation, 'orderFlag': 'OCO',
                   'transactionType': 'SELL', 'exchangeSegment': 'NSE_EQ', 'productType': 'CNC',
                   'orderType': 'LIMIT', 'validity': 'DAY', 'securityId': security,
                   'quantity': quantity, 'disclosedQuantity': 0,
                   'price': float(sl), 'triggerPrice': float(sl),
                   'price1': float(target), 'triggerPrice1': float(target), 'quantity1': quantity}
        return {'identity': identity, 'symbol': symbol, 'quantity': quantity,
                'sl': float(sl), 'target': float(target), 'cmp': float(cmp), 'payload': payload}

    def submit(self, account, symbol, quantity, sl, target):
        proposal = self.proposal(account, symbol, quantity, sl, target)
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS holding_exits (identity TEXT PRIMARY KEY, state TEXT, proposal TEXT, result TEXT, updated TEXT)')
            try:
                db.execute('INSERT INTO holding_exits VALUES (?, ?, ?, ?, ?)',
                           (proposal['identity'], 'SUBMITTING', json.dumps(proposal), '{}', datetime.now(timezone.utc).isoformat()))
            except sqlite3.IntegrityError:
                raise ValueError('A previous submission exists for this holding. Inspect the ledger and Dhan; no automatic retry is allowed.') from None
        try:
            result = self.call('/forever/orders', proposal['payload'])
            if not isinstance(result, dict) or not result.get('orderId'):
                raise ValueError('No broker order ID returned. Submission outcome is uncertain; do not resubmit.')
            state = str(result.get('orderStatus', 'UNKNOWN')).upper()
            if state not in {'PENDING', 'TRANSIT', 'CONFIRM', 'REJECTED', 'CANCELLED', 'EXPIRED'}:
                state = 'UNKNOWN'
        except Exception:
            with closing(sqlite3.connect(self.ledger)) as db, db:
                db.execute('UPDATE holding_exits SET state=? WHERE identity=?', ('UNKNOWN', proposal['identity']))
            raise
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute('UPDATE holding_exits SET state=?, result=?, updated=? WHERE identity=?',
                       (state, json.dumps(result), datetime.now(timezone.utc).isoformat(), proposal['identity']))
        return {'symbol': symbol, 'quantity': quantity, 'sl': proposal['sl'], 'target': proposal['target'], **result}


def main():
    from dhan_data import InstrumentDirectory
    from secret_store import SecretStore
    from server import NoRedirect
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('symbol')
    parser.add_argument('--quantity', required=True, type=int)
    parser.add_argument('--sl', required=True, type=Decimal)
    parser.add_argument('--target', required=True, type=Decimal)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    saved = SecretStore(root / 'runtime' / 'dhan-session.bin').load()
    if not saved:
        raise SystemExit('Connect Dhan with secure remembering enabled first.')

    def call(path, payload=None):
        allowed = {'/profile', '/ip/getIP', '/holdings', '/orders', '/super/orders', '/forever/orders', '/marketfeed/ltp'}
        if path not in allowed or payload is not None and path not in {'/forever/orders', '/marketfeed/ltp'}:
            raise ValueError('Unsupported holding exit request.')
        request = Request('https://api.dhan.co/v2' + path,
                          headers={'access-token': saved['token'], 'client-id': saved['clientId'], 'Content-Type': 'application/json', 'Accept': 'application/json'},
                          data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with build_opener(NoRedirect).open(request, timeout=15) as response:
                result = json.loads(response.read(4_000_000))
        except HTTPError as error:
            code = error.code
            error.close()
            raise ValueError(f'Dhan HTTP {code} on {path}; request not retried. Inspect broker orders before attempting again.') from None
        except (URLError, TimeoutError, OSError):
            raise ValueError('Dhan response unavailable. Request not retried; inspect broker orders.') from None
        if isinstance(result, dict) and result.get('status') in ('failure', 'error'):
            raise ValueError('Dhan rejected the request. No retry performed.')
        return result

    service = HoldingExit(call, InstrumentDirectory(), root / 'runtime' / 'holding-exits.sqlite')
    action = service.submit if args.apply else service.proposal
    result = action(saved['clientId'], args.symbol, args.quantity, args.sl, args.target)
    result.pop('payload', None)
    result.pop('identity', None)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except ValueError as error:
        raise SystemExit(str(error))
