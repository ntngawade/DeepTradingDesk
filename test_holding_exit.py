import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from holding_exit import HoldingExit


class HoldingExitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.writes = []
        self.forever = []
        self.timeout = False
        self.service = HoldingExit(self.call, SimpleNamespace(details=lambda symbol: {'securityId': '14413', 'tickSize': .05}), Path(self.temp.name) / 'exits.sqlite')

    def call(self, path, payload=None):
        if path == '/forever/orders' and payload is not None:
            self.writes.append(payload)
            if self.timeout:
                raise TimeoutError('Simulated uncertain submission')
            return {'orderId': 'test-order', 'orderStatus': 'PENDING'}
        return {'/profile': {'dhanClientId': 'account', 'ddpi': 'Active'}, '/ip/getIP': {'primaryIP': '8.8.8.8'},
                '/holdings': [{'securityId': '14413', 'exchange': 'ALL', 'totalQty': 4, 'availableQty': 4}],
                '/orders': [], '/super/orders': [], '/forever/orders': self.forever,
                '/marketfeed/ltp': {'data': {'NSE_EQ': {'14413': {'last_price': 31325}}}}}[path]

    def submit(self, quantity=4):
        return self.service.submit('account', 'PAGEIND', quantity, 30788, 34799)

    def test_exact_linked_sell_payload_and_duplicate_prevention(self):
        result = self.submit()
        self.assertEqual(result['orderId'], 'test-order')
        payload = self.writes[0]
        self.assertEqual((payload['transactionType'], payload['productType'], payload['orderFlag']), ('SELL', 'CNC', 'OCO'))
        self.assertEqual((payload['price'], payload['triggerPrice'], payload['quantity']), (30788, 30788, 4))
        self.assertEqual((payload['price1'], payload['triggerPrice1'], payload['quantity1']), (34799, 34799, 4))
        with self.assertRaisesRegex(ValueError, 'previous submission'):
            self.submit()
        self.assertEqual(len(self.writes), 1)

    def test_uncertain_result_never_resubmits(self):
        self.timeout = True
        with self.assertRaises(TimeoutError):
            self.submit()
        with self.assertRaisesRegex(ValueError, 'previous submission'):
            self.submit()
        self.assertEqual(len(self.writes), 1)

    def test_existing_broker_trigger_blocks_duplicate(self):
        self.forever = [{'securityId': '14413', 'orderStatus': 'CONFIRM'}]
        with self.assertRaisesRegex(ValueError, 'existing order'):
            self.submit()
        self.assertFalse(self.writes)

    def test_cannot_sell_more_than_available_or_change_exact_prices(self):
        with self.assertRaises(ValueError):
            self.submit(5)
        with self.assertRaisesRegex(ValueError, 'tick size'):
            self.service.submit('account', 'PAGEIND', 4, 30788.01, 34799)
        self.assertFalse(self.writes)


if __name__ == '__main__':
    unittest.main()
