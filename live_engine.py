"""Durable CNC entry monitoring and Super Order submission. No automatic retries."""
import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
ACTIVE = {'ARMED', 'SUBMITTING', 'UNKNOWN', 'ENTRY_PENDING', 'PART_FILLED', 'PROTECTED', 'ACTION_REQUIRED', 'EXIT_TRIGGERED'}


def now():
    return datetime.now(timezone.utc).isoformat()


def regular_session():
    current = datetime.now(IST)
    return current.weekday() < 5 and (9, 15) <= (current.hour, current.minute) < (15, 25)


def positive(value, name, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 or maximum and value > maximum:
        raise ValueError(f'Invalid {name}.')
    return value


def nonnegative(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f'Invalid {name}.')
    return value


def normalize_plan(plan, settings, instrument, trigger=1):
    if not isinstance(plan, dict) or not isinstance(settings, dict):
        raise ValueError('Plan and risk settings are required.')
    if plan.get('status') != 'Watching':
        raise ValueError('Only an enabled, unexecuted Watching plan can be sent live.')
    if plan.get('enabled') is False:
        raise ValueError('This trade plan is disabled.')
    trade_id = plan.get('id')
    if not isinstance(trade_id, str) or not 1 <= len(trade_id) <= 400:
        raise ValueError('A stable trade ID is required.')
    capital = positive(settings.get('capital'), 'capital')
    risk_percent = positive(settings.get('risk'), 'risk percentage', 100)
    trigger = positive(trigger, 'entry proximity percentage', 7)
    tick = Decimal(str(positive(instrument.get('tickSize'), 'exchange tick size')))
    def price(key, rounding):
        value = Decimal(str(positive(plan.get(key), key)))
        return float((value / tick).to_integral_value(rounding=rounding) * tick)
    entry, sl, target = price('entry', ROUND_DOWN), price('sl', ROUND_DOWN), price('target', ROUND_UP)
    if not 0 < sl < entry < target:
        raise ValueError('Delivery BUY requires 0 < stop-loss < entry < target after tick rounding.')
    per_share = Decimal(str(entry)) - Decimal(str(sl))
    quantity = min(int(Decimal(str(capital * risk_percent / 100)) / per_share), int(Decimal(str(capital)) / Decimal(str(entry))))
    if plan.get('qtyMode') == 'manual':
        requested = plan.get('qty')
        if isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= quantity:
            raise ValueError('Manual quantity must be a whole number within the tick-rounded capital and risk limits.')
        quantity = requested
    if quantity < 1:
        raise ValueError('Capital and risk budget cannot fund one share.')
    expiry = plan.get('expiry') or ''
    if expiry and date.fromisoformat(expiry) < datetime.now(IST).date():
        raise ValueError('This trade plan has expired.')
    return {'tradeId': trade_id, 'symbol': plan['symbol'], 'securityId': str(instrument['securityId']),
            'setup': str(plan.get('setup') or '')[:30], 'timeframe': str(plan.get('timeframe') or 'Daily')[:30],
            'notes': str(plan.get('notes') or '')[:200],
            'entry': entry, 'sl': sl, 'target': target, 'quantity': quantity,
            'cash': round(entry * quantity, 2), 'risk': round(float(per_share * quantity), 2),
            'capital': capital, 'triggerPercent': trigger,
            'externalCash': nonnegative(settings.get('externalCash', 0), 'recorded holding capital'),
            'expiry': expiry, 'tickSize': float(tick), 'product': 'CNC', 'orderType': 'MARKET', 'entryRule': 'WITHIN_TRIGGER_BAND_TOUCH_ENTRY_MARKET'}


class LiveEngine:
    def __init__(self, path, broker, is_open=regular_session):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS plans (account TEXT, trade_id TEXT, data TEXT, PRIMARY KEY(account,trade_id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS plan_flags (trade_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS engine_control (account TEXT PRIMARY KEY, manual_pause INTEGER NOT NULL DEFAULT 0)')
        self.db.commit()
        self.lock = threading.RLock()
        self.cycle_lock = threading.Lock()
        self.broker = broker
        self.is_open = is_open
        self.enabled = False  # Startup resumes only after broker reconciliation and valid prerequisites.
        self.enabled_account = None
        self.auto_pause_reason = None
        self.startup_reconciliation_pending = True
        self.control_generation = 0
        self.previews = {}
        self.error = None
        self.stop = threading.Event()
        self.last_reconcile = 0
        self.thread = None
        with self.lock:
            for account, trade_id, raw in self.db.execute('SELECT account,trade_id,data FROM plans').fetchall():
                record = json.loads(raw)
                if record['state'] == 'SUBMITTING':
                    record.update(state='UNKNOWN', message='Restart during submission. Reconciliation required; no retry will be sent.')
                    self._write(account, record)
                elif record['state'] == 'ARMED' and (record.get('orderType') != 'MARKET' or record.get('entryRule') != 'WITHIN_TRIGGER_BAND_TOUCH_ENTRY_MARKET'):
                    record.update(orderType='MARKET', entryRule='WITHIN_TRIGGER_BAND_TOUCH_ENTRY_MARKET', message='Watching CMP; when it comes within 1% and touches planned entry from above, submit one CNC market Super Order with the planned SL and target.')
                    self._write(account, record)

    def _write(self, account, record):
        record['updatedAt'] = now()
        self.db.execute('INSERT OR REPLACE INTO plans VALUES (?,?,?)', (account, record['tradeId'], json.dumps(record)))
        self.db.commit()

    def records(self, account):
        with self.lock:
            return [json.loads(raw) for (raw,) in self.db.execute('SELECT data FROM plans WHERE account=?', (account,))]

    def plan_enabled(self, trade_id):
        row = self.db.execute('SELECT enabled FROM plan_flags WHERE trade_id=?', (trade_id,)).fetchone()
        return row is None or bool(row[0])

    def _manual_pause(self, account):
        if not account:
            return False
        row = self.db.execute('SELECT manual_pause FROM engine_control WHERE account=?', (account,)).fetchone()
        if row is None:
            row = self.db.execute("SELECT manual_pause FROM engine_control WHERE account='*'").fetchone()
        return bool(row[0]) if row else False

    def set_plan_enabled(self, trade_id, enabled):
        if not isinstance(trade_id, str) or not 1 <= len(trade_id) <= 400 or not isinstance(enabled, bool):
            raise ValueError('Valid trade ID and enabled flag required.')
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                records = [(account, json.loads(raw)) for account, raw in self.db.execute('SELECT account,data FROM plans WHERE trade_id=?', (trade_id,))]
                if not enabled and any(record['state'] in ACTIVE and record['state'] != 'ARMED' for _, record in records):
                    raise ValueError('An order is already submitted or filled. Manage it in Dhan; disabling here cannot cancel it.')
                self.db.execute('INSERT OR REPLACE INTO plan_flags VALUES (?,?)', (trade_id, int(enabled)))
                for account, record in records:
                    if not enabled and record['state'] == 'ARMED':
                        record.update(state='DISARMED', message='Trade disabled before order submission.')
                        record['updatedAt'] = now()
                        self.db.execute('UPDATE plans SET data=? WHERE account=? AND trade_id=?', (json.dumps(record), account, trade_id))
                self.db.commit()
                if not enabled:
                    self.previews = {key: value for key, value in self.previews.items() if value['tradeId'] != trade_id}
            except Exception:
                self.db.rollback()
                raise
        return self.status()

    def pause(self, manual=False, automatic=False):
        with self.lock:
            self.control_generation += 1
            self.enabled = False
            if not automatic:
                self.auto_pause_reason = None
            if manual:
                account = self.broker.account() or '*'
                self.db.execute('INSERT OR REPLACE INTO engine_control VALUES (?,1)', (account,))
                self.db.commit()

    def enable(self):
        with self.lock:
            generation = self.control_generation
        account = self.broker.ready()
        self.broker.prerequisites()
        with self.lock:
            if generation != self.control_generation:
                raise ValueError('Live enable was cancelled by a newer Pause request.')
            self.enabled, self.enabled_account = True, account
            self.auto_pause_reason = None
            self.startup_reconciliation_pending = False
            self.db.execute('INSERT OR REPLACE INTO engine_control VALUES (?,0)', (account,))
            self.db.commit()
            self.error = None
        return self.status()

    def status(self):
        account = self.broker.account()
        with self.lock:
            unverified = sum(json.loads(raw)['state'] in ACTIVE for (raw,) in self.db.execute('SELECT data FROM plans')) if not account else 0
            manual_pause = self._manual_pause(account)
            return {'enabled': bool(self.enabled and account == self.enabled_account),
                    'planEnabled': {trade_id: bool(enabled) for trade_id, enabled in self.db.execute('SELECT trade_id,enabled FROM plan_flags')},
                    'account': account, 'records': self.records(account) if account else [],
                    'error': self.error, 'marketSession': self.is_open(), 'triggerPercent': 1,
                    'manualPaused': manual_pause,
                    'unverifiedActiveCount': unverified,
                    'engine': 'CNC_SUPER_ORDER', 'entryRule': 'WITHIN_TRIGGER_BAND_TOUCH_ENTRY_MARKET', 'entryOrderType': 'MARKET', 'serverRequiredForNewEntries': True}

    def _check_capacity(self, snapshot, records, funds):
        active = [r for r in records if r['state'] in ACTIVE and r['tradeId'] != snapshot['tradeId']]
        if any(r['symbol'] == snapshot['symbol'] for r in active):
            raise ValueError('Another active live plan already exists for this stock.')
        external_cash = max([snapshot.get('externalCash', 0)] + [r.get('externalCash', 0) for r in active])
        if external_cash + sum(r['cash'] for r in active) + snapshot['cash'] > snapshot['capital'] + 0.001:
            raise ValueError('Live capital reservation limit exceeded.')
        unsent = sum(r['cash'] for r in active if r['state'] == 'ARMED')
        if unsent + snapshot['cash'] > funds + 0.001:
            raise ValueError('Insufficient available Dhan funds after armed-plan reservations.')

    def preview(self, plan, settings, trigger=1):
        with self.lock:
            if isinstance(plan, dict) and not self.plan_enabled(plan.get('id')):
                raise ValueError('This trade plan is disabled. Enable it before reviewing live execution.')
        account = self.broker.ready()
        if not isinstance(plan, dict) or not isinstance(plan.get('symbol'), str):
            raise ValueError('A stock symbol is required.')
        instrument = self.broker.instrument(plan['symbol'])
        snapshot = normalize_plan(plan, settings, instrument, trigger)
        funds = self.broker.funds()
        self.broker.check_no_existing_exposure(snapshot['securityId'])
        with self.lock:
            records = self.records(account)
            existing = next((r for r in records if r['tradeId'] == snapshot['tradeId']), None)
            if existing and existing['state'] != 'DISARMED':
                raise ValueError('This trade ID already has a live record. Review it; duplicate arming is blocked.')
            self._check_capacity(snapshot, records, funds)
            preview_id = uuid.uuid4().hex
            snapshot.update(account=account, previewId=preview_id, reviewedAt=now(), availableFunds=funds)
            if len(self.previews) > 100:
                self.previews.clear()
            self.previews[preview_id] = snapshot
            return dict(snapshot)

    def arm(self, preview_id):
        account = self.broker.ready()
        with self.lock:
            if not self.enabled or account != self.enabled_account:
                raise ValueError('Enable live CNC automation before queueing this enabled plan.')
            snapshot = self.previews.get(preview_id)
            if not snapshot or snapshot['account'] != account:
                raise ValueError('Create a fresh live-order review first.')
            if (datetime.now(timezone.utc) - datetime.fromisoformat(snapshot['reviewedAt'])).total_seconds() > 120:
                raise ValueError('Review expired. Review the plan again before arming.')
            self.db.execute('BEGIN IMMEDIATE')
            try:
                existing = next((r for r in self.records(account) if r['tradeId'] == snapshot['tradeId']), None)
                if not self.plan_enabled(snapshot['tradeId']):
                    raise ValueError('This trade was disabled after review. Create a fresh review after enabling.')
                if existing and existing['state'] != 'DISARMED':
                    raise ValueError('Duplicate live arming is blocked.')
                self._check_capacity(snapshot, self.records(account), snapshot['availableFunds'])
                record = {k: v for k, v in snapshot.items() if k not in ('previewId', 'availableFunds')}
                record.update(state='ARMED', orderId=None, filledQty=0, correlationId='NTD'+hashlib.sha256((account+'|'+snapshot['tradeId']).encode()).hexdigest()[:26], message=f'Watching CMP; when it comes within {snapshot["triggerPercent"]:g}% and touches planned entry from above, submit one CNC market Super Order with the planned SL and target.')
                self._write(account, record)
                del self.previews[preview_id]
                return dict(record)
            except Exception:
                self.db.rollback()
                raise

    def _claim(self, account, candidate):
        """One durable submission claim even if two app processes share this ledger."""
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                row = self.db.execute('SELECT data FROM plans WHERE account=? AND trade_id=?', (account, candidate['tradeId'])).fetchone()
                current = json.loads(row[0]) if row else None
                if (not self.enabled or account != self.enabled_account or not current or current['state'] != 'ARMED'
                        or current['reviewedAt'] != candidate['reviewedAt'] or not self.plan_enabled(candidate['tradeId'])):
                    self.db.rollback()
                    return None
                current.update(state='SUBMITTING', submittedAt=now(), message='Submitting one CNC market Super Order after planned entry was touched.')
                self._write(account, current)
                return current
            except Exception:
                self.db.rollback()
                raise

    def disarm(self, trade_id):
        account = self.broker.account()
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                record = next((r for r in self.records(account) if r['tradeId'] == trade_id), None)
                if not record or record['state'] != 'ARMED':
                    raise ValueError('Only an unsent ARMED plan can be disarmed. Manage submitted orders in Dhan.')
                record.update(state='DISARMED', message='No broker order was submitted.')
                self._write(account, record)
                return dict(record)
            except Exception:
                self.db.rollback()
                raise

    def reconcile(self, account, records):
        tracked = [r for r in records if r['state'] not in ('ARMED', 'DISARMED', 'REJECTED', 'CLOSED', 'CANCELLED', 'EXPIRED')]
        if not tracked:
            return
        orders = self.broker.orders()
        if not isinstance(orders, list):
            raise ValueError('Dhan Super Order list is unavailable; new entries paused.')
        for record in tracked:
            matches = [o for o in orders if str(o.get('dhanClientId')) == account and
                       ((record.get('orderId') and str(o.get('orderId')) == record['orderId']) or o.get('correlationId') == record['correlationId'])]
            if len(matches) != 1:
                # Dhan's Super Order list contains orders placed on the current
                # day only. Older submitted CNC trades must be checked against
                # historical BUY and SELL fills before remaining unresolved.
                last_check = record.get('historicalCheckAt')
                try:
                    checked_at = datetime.fromisoformat(str(last_check).replace('Z', '+00:00')) if last_check else None
                except ValueError:
                    checked_at = None
                if record.get('filledQty', 0) and (checked_at is None or (datetime.now(timezone.utc)-checked_at).total_seconds() >= 60):
                    try:
                        sale = self.broker.exit_fills(record)
                        record['historicalCheckAt'] = now()
                        if sale:
                            record.update(state='CLOSED', exitPrice=sale['price'], closedQuantity=sale['quantity'],
                                          closedAt=sale.get('exitTradeAt') or now(), entryTradeAt=sale.get('entryTradeAt') or record.get('entryTradeAt'),
                                          exitTradeAt=sale.get('exitTradeAt'), message='Full linked BUY and SELL executions verified in Dhan trade history.')
                        else:
                            held_quantity = getattr(self.broker, 'holding_quantity', lambda _record: None)(record)
                            if held_quantity == record.get('filledQty') and held_quantity:
                                record.update(state='EXIT_TRIGGERED', message=f'Dhan confirms {held_quantity} shares remain held and trade history has no SELL fill. Do not resubmit this plan; manage or restore its exit protection in Dhan. Other enabled plans may continue.')
                            else:
                                record.update(state='ACTION_REQUIRED', message=f'Order is no longer in Dhan’s current Super Order list and no full linked sale is verified (Dhan holding: {held_quantity if held_quantity is not None else "unavailable"} / {record.get("filledQty", 0)} shares). Check Dhan before enabling new entries.')
                    except Exception as error:
                        record['historicalCheckAt'] = now()
                        record.update(state='ACTION_REQUIRED', message='Could not verify the old Super Order in Dhan historical trade history: '+str(error)[:220])
                else:
                    if record.get('state') == 'EXIT_TRIGGERED':
                        # Keep a verified open holding out of the global pause while
                        # historical trade checks are rate-limited between cycles.
                        continue
                    try:
                        held_quantity = getattr(self.broker, 'holding_quantity', lambda _record: None)(record)
                    except Exception:
                        held_quantity = None
                    if held_quantity == record.get('filledQty') and held_quantity:
                        record.update(state='EXIT_TRIGGERED', holdingVerifiedAt=now(),
                                      message=f'Dhan confirms {held_quantity} shares remain held and trade history has no SELL fill. Do not resubmit this plan; manage or restore its exit protection in Dhan. Other enabled plans may continue.')
                    else:
                        record.update(state='ACTION_REQUIRED', message='Order is outside Dhan’s current Super Order list; waiting for the next historical trade verification. No resubmission will occur.')
            else:
                order = matches[0]
                if (str(order.get('securityId')) != record['securityId'] or order.get('transactionType') != 'BUY'
                        or order.get('productType') != 'CNC' or order.get('exchangeSegment') != 'NSE_EQ'
                        or int(order.get('quantity') or 0) != record['quantity']):
                    record.update(state='ACTION_REQUIRED', message='Broker order does not match the reviewed CNC plan. Check Dhan; no resubmission.')
                    with self.lock:
                        self._write(account, record)
                    continue
                record['orderId'] = str(order['orderId'])
                record['brokerStatus'] = order.get('orderStatus')
                previous_filled = record.get('filledQty', 0)
                record['filledQty'] = previous_filled if order.get('filledQty') is None else int(order['filledQty'])
                record['averageFillPrice'] = float(order.get('averageTradedPrice') or record.get('averageFillPrice') or 0)
                entry_leg = next((leg for leg in order.get('legDetails', []) if leg.get('legName') == 'ENTRY_LEG'), {})
                entry_stamp = entry_leg.get('exchangeTime') or order.get('exchangeTime')
                if record['filledQty'] and record['averageFillPrice'] <= 0:
                    fills = self.broker.entry_fills(record)
                    if fills:
                        record['averageFillPrice'] = fills['price']
                        entry_stamp = fills.get('tradeAt') or entry_stamp
                if record['filledQty'] and isinstance(entry_stamp, str) and len(entry_stamp) == 19:
                    record['entryTradeAt'] = entry_stamp
                if not previous_filled <= record['filledQty'] <= record['quantity']:
                    record.update(state='ACTION_REQUIRED', message='Dhan fill quantity conflicts with the app ledger. Check the broker order and trade book.')
                    with self.lock:
                        self._write(account, record)
                    continue
                if not math.isfinite(record['averageFillPrice']) or record['filledQty'] and record['averageFillPrice'] <= 0:
                    record.update(state='ACTION_REQUIRED', message='Dhan confirmed an entry fill but no matching valid average price was found in the trade book. Check the Dhan order and trade book.')
                    with self.lock:
                        self._write(account, record)
                    continue
                legs = order.get('legDetails') or []
                record['brokerLegs'] = [{'orderId': str(leg.get('orderId') or ''), 'legName': leg.get('legName')} for leg in legs]
                record['legs'] = [{k: leg.get(k) for k in ('legName','orderStatus','price','remainingQuantity','triggeredQuantity')} for leg in legs]
                status = order.get('orderStatus')
                drift = any(isinstance(leg.get('price'), (int, float)) and leg['price'] > 0 and
                            abs(leg['price'] - record['sl' if leg.get('legName') == 'STOP_LOSS_LEG' else 'target']) > record['tickSize']/2
                            for leg in legs if leg.get('legName') in ('STOP_LOSS_LEG', 'TARGET_LEG'))
                if drift and status != 'CLOSED' and not any(leg.get('orderStatus') in ('TRIGGERED', 'TRADED') for leg in legs):
                    record.update(state='ACTION_REQUIRED', message='Broker SL or target differs from the reviewed plan. Check the changed levels and live risk in Dhan.')
                    with self.lock:
                        self._write(account, record)
                    continue
                if status in ('REJECTED', 'CANCELLED', 'EXPIRED') and not record['filledQty']:
                    record.update(state=status, message='Broker entry ended without a fill.')
                elif status == 'CLOSED' or any(leg.get('orderStatus') in ('TRIGGERED', 'TRADED') for leg in legs):
                    # A trigger is not a fill: only actual linked SELL executions close the journal.
                    sale = self.broker.exit_fills(record) if record['filledQty'] else None
                    if sale:
                        record.update(state='CLOSED', exitPrice=sale['price'], closedQuantity=sale['quantity'], closedAt=sale.get('exitTradeAt') or now(),
                                      entryTradeAt=sale.get('entryTradeAt'), exitTradeAt=sale.get('exitTradeAt'),
                                      message='Entry and full linked SELL executions verified in Dhan.')
                    else:
                        record.update(state='EXIT_TRIGGERED', message='Broker exit triggered; waiting for actual linked SELL fills. Reservation retained.')
                elif record['filledQty']:
                    healthy = {leg.get('legName') for leg in legs if leg.get('orderStatus') in ('PENDING', 'TRANSIT', 'TRIGGERED', 'TRADED')}
                    if not {'TARGET_LEG', 'STOP_LOSS_LEG'} <= healthy:
                        record.update(state='ACTION_REQUIRED', message='Entry has fills but both exit legs are not confirmed healthy. Check protection in Dhan immediately.')
                    else:
                        record.update(state='PROTECTED' if record['filledQty'] == record['quantity'] else 'PART_FILLED', message='Entry fill and linked exit-leg statuses reported by Dhan.')
                else:
                    record.update(state='ENTRY_PENDING', message='Broker entry pending; exit legs activate after entry fills.')
            with self.lock:
                self._write(account, record)

    def cycle(self):
        if not self.cycle_lock.acquire(blocking=False):
            return
        try:
            account = self.broker.account()
            if not account:
                return
            records = self.records(account)
            self.reconcile(account, records)
            records = self.records(account)
            blockers = [r for r in records if r['state'] in ('UNKNOWN', 'ACTION_REQUIRED')]
            if blockers:
                details = ' / '.join(f"{r['symbol']}: {r.get('message') or r['state']}" for r in blockers)
                self.error = 'Live entries paused for broker review. ' + details
                self.auto_pause_reason = 'broker review'
                self.enabled_account = account
                self.startup_reconciliation_pending = False
                self.pause(automatic=True)
                return
            if self.auto_pause_reason == 'broker review' and self.enabled_account == account and not self._manual_pause(account):
                self.enabled, self.error, self.auto_pause_reason = True, None, None
            if self.startup_reconciliation_pending:
                enabled_flags = self.db.execute('SELECT 1 FROM plan_flags WHERE enabled=1 LIMIT 1').fetchone()
                if enabled_flags and not self._manual_pause(account):
                    self.broker.prerequisites()
                    self.enabled, self.enabled_account, self.error = True, account, None
                self.startup_reconciliation_pending = False
            if not self.enabled or account != self.enabled_account or not self.is_open():
                return
            armed_symbols = sorted({r['symbol'] for r in records if r['state'] == 'ARMED'})
            quotes = self.broker.quotes(armed_symbols)['quotes'] if armed_symbols else {}
            for record in records:
                if record['state'] != 'ARMED':
                    continue
                if record['expiry'] and date.fromisoformat(record['expiry']) < datetime.now(IST).date():
                    record.update(state='EXPIRED', message='Plan expired before order submission.')
                    with self.lock:
                        self._write(account, record)
                    continue
                quote = quotes.get(record['symbol'])
                if not quote:
                    raise ValueError('Dhan CMP missing for '+record['symbol']+'. New entries paused.')
                price = positive(quote['price'], 'live CMP')
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(quote['fetchedAt'])).total_seconds()
                if not 0 <= age <= 15:
                    raise ValueError('Dhan quote is stale. New entries paused.')
                near = abs(price-record['entry'])/record['entry']*100 <= record['triggerPercent']+1e-9
                with self.lock:
                    self.db.execute('BEGIN IMMEDIATE')
                    current = next((r for r in self.records(account) if r['tradeId'] == record['tradeId']), None)
                    if not current or current['state'] != 'ARMED' or current['reviewedAt'] != record['reviewedAt']:
                        self.db.rollback()
                        continue
                touched_from_above = near and price <= record['entry']
                current.update(lastCmp=price, lastCmpAt=quote['fetchedAt'], message=('Entry touched inside the trigger band; checking market-order readiness.' if touched_from_above else f'Waiting for CMP to come within {record["triggerPercent"]:g}% and touch Entry from above.'))
                with self.lock:
                    self._write(account, current)
                record = current
                if not touched_from_above:
                    continue
                if price <= record['sl'] or price >= record['target']:
                    raise ValueError('CMP has crossed the planned stop-loss or target. Review the plan.')
                self.broker.ready()
                self.broker.check_no_existing_exposure(record['securityId'])
                self._check_capacity(record, self.records(account), self.broker.funds())
                # Funds/exposure checks may take seconds. Recheck price and session immediately before claiming.
                latest = self.broker.quotes([record['symbol']])['quotes'].get(record['symbol'])
                if not latest or not self.is_open():
                    continue
                latest_price = positive(latest['price'], 'live CMP')
                latest_age = (datetime.now(timezone.utc) - datetime.fromisoformat(latest['fetchedAt'])).total_seconds()
                latest_near = abs(latest_price-record['entry'])/record['entry']*100 <= record['triggerPercent']+1e-9
                if not 0 <= latest_age <= 15 or not latest_near or latest_price > record['entry']:
                    continue
                if latest_price <= record['sl'] or latest_price >= record['target']:
                    raise ValueError('Latest CMP crossed SL or target. Review the live plan.')
                record = self._claim(account, record)
                if record is None:
                    continue
                try:
                    result = self.broker.submit(record)
                    order_id = result.get('orderId')
                    if not order_id:
                        raise ValueError('No broker order ID was returned.')
                    record.update(orderId=str(order_id), state='REJECTED' if result.get('orderStatus') == 'REJECTED' else 'ENTRY_PENDING', message='Market Super Order acknowledged after Entry touch; checking fill and linked SL/target with Dhan.')
                except Exception as error:
                    definitive = getattr(error, 'definitive_rejection', False)
                    record.update(state='REJECTED' if definitive else 'UNKNOWN', message='Dhan rejected the order. Check IP, funds and permissions; no automatic retry.' if definitive else 'Submission outcome uncertain. Check Dhan; automatic retry is blocked.')
                    self.pause()
                with self.lock:
                    self._write(account, record)
                break  # At most one new order per cycle; later plans get fresh quotes/funds.
        except Exception as error:
            self.error = str(error)
            self.pause()
        finally:
            self.cycle_lock.release()

    def start(self):
        def work():
            while not self.stop.is_set():
                started = time.monotonic()
                self.cycle()
                self.stop.wait(max(0, 1.05 - (time.monotonic() - started)))
        self.thread = threading.Thread(target=work, daemon=True, name='CNC-live-engine')
        self.thread.start()
