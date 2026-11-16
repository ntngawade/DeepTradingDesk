import copy
import tempfile
import unittest
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from live_engine import LiveEngine, normalize_plan, entry_touched


SETTINGS = {'capital': 500000, 'risk': 1}
PLAN = {'id': 'PLAN-1', 'symbol': 'JIOFIN', 'entry': 1000, 'sl': 980, 'target': 1040, 'status': 'Watching'}


class FakeBroker:
    def __init__(self):
        self.client_id = '123'
        self.balance = 500000
        self.price = 1000
        self.age = 0
        self.sent = []
        self.book = []
        self.exit = None
        self.uncertain = False
        self.exposure = False

    def account(self):
        return self.client_id

    def ready(self):
        if not self.client_id:
            raise ValueError('Disconnected')
        return self.client_id

    def prerequisites(self):
        self.ready()

    def instrument(self, symbol):
        return {'securityId': '18143' if symbol == 'JIOFIN' else '11536', 'tickSize': 0.05}

    def funds(self):
        return self.balance

    def check_no_existing_exposure(self, security_id):
        if self.exposure:
            raise ValueError('Existing exposure')

    def quotes(self, symbols):
        return {'quotes': {s: {'price': self.price, 'fetchedAt': (datetime.now(timezone.utc)-timedelta(seconds=self.age)).isoformat()} for s in symbols}}

    def orders(self):
        return copy.deepcopy(self.book)

    def exit_fills(self, record):
        return self.exit

    def submit(self, record):
        self.sent.append(copy.deepcopy(record))
        self.book.append({'dhanClientId': self.client_id, 'orderId': 'ORDER1', 'correlationId': record['correlationId'],
                          'securityId': record['securityId'], 'transactionType': 'BUY', 'exchangeSegment': 'NSE_EQ',
                          'productType': 'CNC', 'quantity': record['quantity'], 'orderStatus': 'PENDING',
                          'filledQty': 0, 'averageTradedPrice': 0,
                          'legDetails': [{'orderId': 'SL1', 'legName': 'STOP_LOSS_LEG', 'orderStatus': 'PENDING'},
                                         {'orderId': 'TP1', 'legName': 'TARGET_LEG', 'orderStatus': 'PENDING'}]})
        if self.uncertain:
            raise TimeoutError('Simulated lost acknowledgement after acceptance')
        return {'orderId': 'ORDER1', 'orderStatus': 'PENDING'}


class LiveEngineTests(unittest.TestCase):
    def test_disabled_plan_blocks_preview_and_default_is_enabled(self):
        self.assertTrue(self.engine.plan_enabled(PLAN['id']))
        self.engine.set_plan_enabled(PLAN['id'], False)
        with self.assertRaisesRegex(ValueError, 'disabled'):
            self.engine.preview(PLAN, SETTINGS)
        with self.assertRaisesRegex(ValueError, 'disabled'):
            normalize_plan({**PLAN,'enabled':False},SETTINGS,{'securityId':'18143','tickSize':0.05})
        self.assertFalse(self.broker.sent)

    def test_disable_disarms_unsent_plan_and_enable_does_not_rearm(self):
        self.arm()
        self.engine.set_plan_enabled(PLAN['id'],False)
        self.assertEqual(self.record()['state'],'DISARMED')
        self.engine.cycle()
        self.assertFalse(self.broker.sent)
        self.engine.set_plan_enabled(PLAN['id'],True)
        self.engine.cycle()
        self.assertEqual(self.record()['state'],'DISARMED')
        self.assertFalse(self.broker.sent)

    def test_disabling_submitted_order_is_rejected_without_cancelling_legs(self):
        self.arm();self.engine.cycle()
        with self.assertRaisesRegex(ValueError,'already submitted'):
            self.engine.set_plan_enabled(PLAN['id'],False)
        self.assertTrue(self.engine.plan_enabled(PLAN['id']))
        self.assertEqual(len(self.broker.sent),1)
        self.assertEqual(len(self.broker.book[0]['legDetails']),2)
    def test_manual_quantity_is_honored_only_within_risk_and_capital(self):
        instrument={'securityId':'18143','tickSize':0.05}
        result=normalize_plan({**PLAN,'qtyMode':'manual','qty':100},SETTINGS,instrument)
        self.assertEqual(result['quantity'],100)
        self.assertEqual(result['cash'],100000)
        for quantity in (251,0,1.5,True):
            with self.assertRaises(ValueError):
                normalize_plan({**PLAN,'qtyMode':'manual','qty':quantity},SETTINGS,instrument)
    def test_proximity_is_not_entry_and_crossing_works_from_either_side(self):
        stamp = datetime.now(timezone.utc)
        record = {'entry': 1000}
        self.assertFalse(entry_touched(record, 999, stamp.isoformat()))
        self.assertFalse(entry_touched(record, 1001, stamp.isoformat()))
        self.assertTrue(entry_touched(record, 1000, stamp.isoformat()))
        record.update(lastCmp=1001, lastCmpAt=(stamp-timedelta(seconds=5)).isoformat())
        self.assertTrue(entry_touched(record, 999, stamp.isoformat()))
        self.assertFalse(entry_touched(record, 1000.5, stamp.isoformat()))
        record['lastCmp'] = 999
        self.assertTrue(entry_touched(record, 1001, stamp.isoformat()))
        record['lastCmpAt'] = (stamp-timedelta(minutes=2)).isoformat()
        self.assertFalse(entry_touched(record, 1001, stamp.isoformat()))

    def test_initial_price_below_entry_does_not_buy_immediately(self):
        self.arm()
        self.broker.price = 999
        self.engine.cycle()
        self.assertFalse(self.broker.sent)
        self.broker.price = 1000
        self.engine.cycle()
        self.assertEqual(len(self.broker.sent), 1)
        self.assertEqual(self.broker.sent[0]['entry'], 1000)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.broker = FakeBroker()
        self.path = Path(self.temp.name)/'live.sqlite'
        self.engine = LiveEngine(self.path, self.broker, is_open=lambda: True)

    def tearDown(self):
        self.engine.db.close()
        self.temp.cleanup()

    def arm(self, plan=None, settings=None):
        self.engine.enable()
        review = self.engine.preview(plan or PLAN, settings or SETTINGS)
        self.engine.arm(review['previewId'])
        return review

    def record(self):
        return self.engine.records('123')[0]

    def test_one_percent_limit_entry_partial_fills_protection_and_actual_exit(self):
        review = self.arm()
        self.assertEqual(review['quantity'], 250)
        self.broker.price = 1020
        self.engine.cycle()
        self.assertEqual(len(self.broker.sent), 0)
        self.broker.price = 1010  # 1% is preparation only.
        self.engine.cycle()
        self.assertEqual(len(self.broker.sent), 0)
        self.broker.price = 1000  # Touch planned entry.
        self.engine.cycle()
        self.assertEqual(len(self.broker.sent), 1)
        self.assertEqual(self.record()['state'], 'ENTRY_PENDING')
        self.assertEqual(self.broker.sent[0]['entry'], 1000)
        self.assertLessEqual(len(self.record()['correlationId']), 30)
        self.broker.book[0].update(filledQty=100, averageTradedPrice=999, orderStatus='PART_TRADED')
        self.engine.cycle()
        self.assertEqual(self.record()['state'], 'PART_FILLED')
        self.broker.book[0].update(filledQty=250, averageTradedPrice=999, orderStatus='TRADED')
        self.engine.cycle()
        self.assertEqual(self.record()['state'], 'PROTECTED')
        self.broker.book[0]['orderStatus'] = 'CLOSED'
        self.engine.cycle()
        self.assertEqual(self.record()['state'], 'EXIT_TRIGGERED', 'Trigger alone must not create realized P/L')
        self.broker.exit = {'quantity': 250, 'price': 1041,'entryTradeAt':'2026-11-16 09:20:00','exitTradeAt':'2026-11-16 10:45:00'}
        self.engine.cycle()
        self.assertEqual(self.record()['state'], 'CLOSED')
        self.assertEqual(self.record()['exitPrice'], 1041)
        self.assertEqual(self.record()['entryTradeAt'],'2026-11-16 09:20:00')
        self.assertEqual(self.record()['exitTradeAt'],'2026-11-16 10:45:00')
        self.engine.cycle()
        self.assertEqual(len(self.broker.sent), 1)

    def test_live_disabled_by_default_and_explicit_review_required(self):
        self.assertFalse(self.engine.status()['enabled'])
        review = self.engine.preview(PLAN, SETTINGS)
        with self.assertRaises(ValueError):
            self.engine.arm(review['previewId'])
        self.engine.cycle()
        self.assertFalse(self.broker.sent)

    def test_duplicate_arm_and_duplicate_trade_id_blocked(self):
        review = self.arm()
        with self.assertRaises(ValueError):
            self.engine.arm(review['previewId'])
        with self.assertRaises(ValueError):
            self.engine.preview(PLAN, SETTINGS)
        self.engine.cycle()
        with self.assertRaises(ValueError):
            self.engine.disarm(PLAN['id'])

    def test_pause_does_not_cancel_existing_broker_protection(self):
        self.arm()
        self.engine.cycle()
        self.engine.pause()
        self.broker.book[0].update(filledQty=250, averageTradedPrice=1000, orderStatus='TRADED')
        self.engine.cycle()
        self.assertEqual(self.record()['state'], 'PROTECTED')
        self.assertEqual(len(self.broker.sent), 1)

    def test_lost_acknowledgement_is_reconciled_without_retry(self):
        self.arm()
        self.broker.uncertain = True
        self.engine.cycle()
        self.assertEqual(self.record()['state'], 'UNKNOWN')
        self.assertFalse(self.engine.enabled)
        self.engine.cycle()
        self.assertEqual(self.record()['state'], 'ENTRY_PENDING')
        self.assertEqual(len(self.broker.sent), 1)

    def test_restart_during_submission_is_never_resubmitted(self):
        self.arm()
        record = self.record()
        record['state'] = 'SUBMITTING'
        self.engine._write('123', record)
        self.engine.db.close()
        self.engine = LiveEngine(self.path, self.broker, is_open=lambda: True)
        self.assertFalse(self.engine.enabled)
        self.assertEqual(self.record()['state'], 'UNKNOWN')
        self.engine.enable()
        self.engine.cycle()
        self.assertEqual(self.record()['state'], 'ACTION_REQUIRED')
        self.assertFalse(self.broker.sent)

    def test_missing_exit_protection_stops_new_entries(self):
        self.arm()
        self.engine.cycle()
        self.broker.book[0].update(filledQty=250, averageTradedPrice=1000, orderStatus='TRADED', legDetails=[])
        self.engine.cycle()
        self.assertEqual(self.record()['state'], 'ACTION_REQUIRED')
        self.assertFalse(self.engine.enabled)

    def test_stale_quotes_closed_session_and_funds_checks(self):
        self.arm()
        self.engine.is_open = lambda: False
        self.engine.cycle()
        self.assertFalse(self.broker.sent)
        self.engine.is_open = lambda: True
        self.broker.age = 20
        self.engine.cycle()
        self.assertFalse(self.engine.enabled)
        self.assertFalse(self.broker.sent)
        self.broker.age = 0
        self.broker.balance = 1000
        self.engine.enable()
        self.engine.cycle()
        self.assertFalse(self.broker.sent)

    def test_per_trade_risk_without_aggregate_portfolio_cap(self):
        self.arm(settings=SETTINGS)
        settings = {**SETTINGS, 'portfolioRisk': 0.01}
        review = self.engine.preview({**PLAN, 'id': 'PLAN-2', 'symbol': 'TCS'}, settings)
        self.assertEqual(review['risk'], 5000)
        self.broker.exposure = True
        self.engine.cycle()
        self.assertFalse(self.broker.sent)

    def test_disarm_before_submission_and_tick_rounding(self):
        review = self.arm({**PLAN, 'entry': 1000.03, 'sl': 980.02, 'target': 1040.01})
        self.assertEqual((review['entry'], review['sl'], review['target']), (1000, 980, 1040.05))
        self.engine.disarm(PLAN['id'])
        self.engine.cycle()
        self.assertEqual(self.record()['state'], 'DISARMED')
        self.assertFalse(self.broker.sent)

    def test_two_process_connections_cannot_claim_the_same_submission(self):
        self.arm()
        second = LiveEngine(self.path, self.broker, is_open=lambda: True)
        second.enabled, second.enabled_account = True, '123'
        record = self.record()
        claimed = []
        def claim(engine):
            claimed.append(engine._claim('123', record))
        workers = [threading.Thread(target=claim, args=(engine,)) for engine in (self.engine, second)]
        try:
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            self.assertEqual(sum(result is not None for result in claimed), 1)
            self.assertEqual(self.record()['state'], 'SUBMITTING')
        finally:
            second.db.close()

    def test_review_expiry_and_account_switch_prevent_arming(self):
        self.engine.enable()
        review = self.engine.preview(PLAN, SETTINGS)
        self.engine.previews[review['previewId']]['reviewedAt'] = (datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.engine.arm(review['previewId'])
        review = self.engine.preview(PLAN, SETTINGS)
        self.broker.client_id = '456'
        with self.assertRaises(ValueError):
            self.engine.arm(review['previewId'])
        self.assertFalse(self.broker.sent)

    def test_recorded_holding_risk_does_not_create_an_aggregate_cap(self):
        review = self.engine.preview(PLAN, {**SETTINGS, 'externalRisk': 220000})
        self.assertEqual(review['risk'], 5000)
        self.assertFalse(self.broker.sent)

    def test_recorded_holding_cash_remains_reserved(self):
        with self.assertRaisesRegex(ValueError, 'capital reservation'):
            self.engine.preview(PLAN, {**SETTINGS, 'externalCash': 300000})

    def test_pause_cancels_in_progress_enable(self):
        started, proceed = threading.Event(), threading.Event()
        errors = []
        def prerequisites():
            started.set()
            proceed.wait(3)
        self.broker.prerequisites = prerequisites
        def enable():
            try:
                self.engine.enable()
            except ValueError as error:
                errors.append(str(error))
        worker = threading.Thread(target=enable)
        worker.start()
        self.assertTrue(started.wait(2))
        self.engine.pause()
        proceed.set()
        worker.join()
        self.assertFalse(self.engine.enabled)
        self.assertTrue(errors)


if __name__ == '__main__':
    unittest.main()
