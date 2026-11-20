import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError
from live_broker import DhanLiveBroker, OrderRejected
from server import DhanSession


class Response:
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, size):
        return json.dumps(self.data).encode()


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.session = DhanSession()
        self.session.token = 'fake-test-token-not-live'
        self.session.profile = {'dhanClientId': '123', 'ddpi': 'Active'}
        self.opener = Mock()
        self.broker = DhanLiveBroker(self.session, None, lambda: self.opener)

    def test_only_cnc_market_super_order_with_plan_exits_is_submitted(self):
        self.opener.open.return_value = Response({'orderId': 'ORDER1', 'orderStatus': 'PENDING'})
        result = self.broker.submit({'account': '123', 'correlationId': 'test-correlation', 'securityId': '18143',
                                     'quantity': 5, 'entry': 100, 'sl': 98, 'target': 104})
        self.assertEqual(result['orderId'], 'ORDER1')
        request = self.opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://api.dhan.co/v2/super/orders')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual((payload['productType'], payload['orderType'], payload['transactionType']), ('CNC', 'MARKET', 'BUY'))
        self.assertEqual((payload['price'], payload['stopLossPrice'], payload['targetPrice']), (0, 98, 104))
        self.assertEqual(payload['trailingJump'], 0)
        self.assertNotIn(self.session.token, request.data.decode())

    def test_ddpi_and_static_ip_configuration_are_required(self):
        self.session.profile['ddpi'] = 'Deactive'
        with self.assertRaisesRegex(ValueError, 'DDPI'):
            self.broker.ready()
        self.session.profile['ddpi'] = 'Active'
        self.opener.open.return_value = Response({'primaryIP': '', 'secondaryIP': ''})
        with self.assertRaisesRegex(ValueError, 'static public'):
            self.broker.prerequisites()

    def test_read_adapter_cannot_post_arbitrary_orders_or_ip_changes(self):
        for endpoint in ('/orders', '/ip/getIP', '/ip/setIP', '/forever/orders'):
            with self.assertRaises(ValueError):
                self.broker.call(endpoint, {'quantity': 10})
        self.opener.open.assert_not_called()

    def test_explicit_broker_rejection_is_not_an_ambiguous_timeout(self):
        self.opener.open.side_effect = HTTPError('https://api.dhan.co/v2/super/orders', 403, 'Forbidden', {}, io.BytesIO(b''))
        with self.assertRaises(OrderRejected):
            self.broker.call('/super/orders', {'quantity': 10})

    def test_exit_fills_require_linked_order_ids_and_actual_quantity(self):
        record = {'orderId': 'MAIN', 'account': '123', 'securityId': '18143', 'filledQty': 5,
                  'brokerLegs': [{'orderId': 'SELL'}]}
        trade = {'orderId': 'SELL', 'dhanClientId': '123', 'securityId': '18143', 'transactionType': 'SELL',
                 'productType': 'CNC', 'exchangeSegment': 'NSE_EQ', 'exchangeTradeId': 'T1', 'tradedQuantity': 5, 'tradedPrice': 105,
                 'exchangeTime':'2026-11-16 10:05:00'}
        entry = {'orderId':'MAIN','dhanClientId':'123','securityId':'18143','transactionType':'BUY','productType':'CNC',
                 'exchangeSegment':'NSE_EQ','exchangeTradeId':'B1','tradedQuantity':5,'tradedPrice':100,'exchangeTime':'2026-11-16 09:20:00'}
        self.opener.open.return_value = Response([entry, trade, {**trade, 'orderId': 'UNRELATED', 'exchangeTradeId': 'T2'}])
        self.assertEqual(self.broker.exit_fills(record), {'quantity': 5, 'price': 105,'entryTradeAt':'2026-11-16 09:20:00','exitTradeAt':'2026-11-16 10:05:00'})
        self.opener.open.return_value = Response([{**trade, 'tradedQuantity': 2}])
        self.assertIsNone(self.broker.exit_fills(record))
        self.opener.open.return_value = Response([trade, trade])
        with self.assertRaises(ValueError):
            self.broker.exit_fills(record)

    def test_old_super_order_exit_is_verified_from_historical_trade_api(self):
        record = {'orderId': 'MAIN', 'account': '123', 'securityId': '18143', 'filledQty': 5,
                  'submittedAt': '2026-11-16T04:00:00+00:00', 'brokerLegs': [{'orderId': 'SELL'}]}
        entry = {'orderId':'MAIN','dhanClientId':'123','securityId':'18143','transactionType':'BUY','productType':'CNC',
                 'exchangeSegment':'NSE_EQ','exchangeTradeId':'B1','tradedQuantity':5,'tradedPrice':100,
                 'exchangeTime':'2026-11-16 09:20:00'}
        sale = {'orderId':'SELL','dhanClientId':'123','securityId':'18143','transactionType':'SELL','productType':'CNC',
                'exchangeSegment':'NSE_EQ','exchangeTradeId':'S1','tradedQuantity':5,'tradedPrice':105,
                'exchangeTime':'2026-11-16 10:05:00'}
        self.opener.open.side_effect = [Response([]), Response([entry, sale])]
        result = self.broker.exit_fills(record)
        self.assertEqual(result, {'quantity':5,'price':105,'entryTradeAt':'2026-11-16 09:20:00','exitTradeAt':'2026-11-16 10:05:00'})
        history_url = self.opener.open.call_args_list[1].args[0].full_url
        self.assertIn('https://api.dhan.co/v2/trades/2026-11-16/', history_url)
        self.assertTrue(history_url.endswith('/0'))

    def test_historical_leg_sell_id_is_reconciled_at_the_planned_stop(self):
        record = {'orderId':'MAIN','account':'123','securityId':'18143','filledQty':5,'sl':98,'target':110,'tickSize':0.05,
                  'submittedAt':'2026-11-16T04:00:00+00:00','brokerLegs':[{'orderId':'MAIN'}]}
        entry = {'orderId':'MAIN','dhanClientId':'123','securityId':'18143','transactionType':'BUY','productType':'CNC',
                 'exchangeSegment':'NSE_EQ','exchangeTradeId':'B1','tradedQuantity':5,'tradedPrice':100,
                 'exchangeTime':'2026-11-16T09:20:00'}
        sale = {'orderId':'CHILD-SELL','dhanClientId':'123','securityId':'18143','transactionType':'SELL','productType':'CNC',
                'exchangeSegment':'NSE_EQ','exchangeTradeId':'S1','tradedQuantity':5,'tradedPrice':98,
                'exchangeTime':'2026-11-16T10:05:00'}
        self.opener.open.side_effect=[Response([]),Response([entry,sale])]
        result=self.broker.exit_fills(record)
        self.assertEqual(result,{'quantity':5,'price':98,'entryTradeAt':'2026-11-16T09:20:00','exitTradeAt':'2026-11-16T10:05:00'})


if __name__ == '__main__':
    unittest.main()
