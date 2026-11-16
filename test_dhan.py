import json
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import server


PROFILE = {'dhanClientId': '1234567890', 'tokenValidity': '15/11/2026 09:00',
           'dataPlan': 'Active'}
TOKEN = 'test-token-not-a-real-secret'


class ConnectionTests(unittest.TestCase):
    def test_trade_history_returns_only_matching_cnc_fills_and_groups_split_trades(self):
        session=server.DhanSession()
        with patch.object(server,'dhan_profile',return_value=PROFILE):session.connect(PROFILE['dhanClientId'],TOKEN)
        rows=[
            {'dhanClientId':PROFILE['dhanClientId'],'orderId':'buy-1','securityId':'1234','transactionType':'BUY','exchangeSegment':'NSE_EQ','productType':'CNC','tradedQuantity':4,'tradedPrice':3999.00,'exchangeTime':'2026-10-10 09:20:01'},
            {'dhanClientId':PROFILE['dhanClientId'],'orderId':'buy-1','securityId':'1234','transactionType':'BUY','exchangeSegment':'NSE_EQ','productType':'CNC','tradedQuantity':6,'tradedPrice':4000.5666667,'exchangeTime':'2026-10-10 09:20:05'},
            {'dhanClientId':PROFILE['dhanClientId'],'orderId':'sell-1','securityId':'1234','transactionType':'SELL','exchangeSegment':'NSE_EQ','productType':'CNC','tradedQuantity':10,'tradedPrice':4401.20,'exchangeTime':'2026-10-12 10:30:00'},
            {'dhanClientId':PROFILE['dhanClientId'],'orderId':'intraday','securityId':'1234','transactionType':'BUY','exchangeSegment':'NSE_EQ','productType':'INTRADAY','tradedQuantity':10,'tradedPrice':4000,'exchangeTime':'2026-10-10 09:20:00'},
            {'dhanClientId':'other-account','orderId':'other','securityId':'1234','transactionType':'SELL','exchangeSegment':'NSE_EQ','productType':'CNC','tradedQuantity':10,'tradedPrice':4401.2,'exchangeTime':'2026-10-12 10:30:00'},
        ]
        with patch.object(server.INSTRUMENTS,'resolve',return_value={'CUMMINSIND':1234}),patch.object(server,'dhan_read',side_effect=[rows,[]]) as read:
            result=session.trade_history('CUMMINSIND','2026-10-01','2026-10-31')
        self.assertEqual(read.call_count,2)
        self.assertEqual([row['orderId'] for row in result['orders']],['buy-1','sell-1'])
        self.assertEqual(result['orders'][0]['quantity'],10)
        self.assertAlmostEqual(result['orders'][0]['averagePrice'],3999.94,places=2)
        self.assertEqual(result['orders'][0]['firstTradeAt'],'2026-10-10 09:20:01')
        self.assertEqual(result['orders'][1]['lastTradeAt'],'2026-10-12 10:30:00')
        self.assertFalse(result['truncated'])

    def test_trade_history_rejects_bad_ranges_before_dhan_calls(self):
        session=server.DhanSession()
        with patch.object(server,'dhan_profile',return_value=PROFILE):session.connect(PROFILE['dhanClientId'],TOKEN)
        with patch.object(server,'dhan_read') as read:
            for from_date,to_date in [('2026-02-30','2026-03-01'),('2026-03-01','2026-02-28'),('2020-01-01','2026-01-01')]:
                with self.assertRaises(ValueError):session.trade_history('CUMMINSIND',from_date,to_date)
            read.assert_not_called()

    def test_portfolio_reads_actual_quantities_without_data_subscription(self):
        session = server.DhanSession()
        with patch.object(server, 'dhan_profile', return_value=PROFILE):
            session.connect(PROFILE['dhanClientId'], TOKEN)
        holdings = [{'tradingSymbol': 'BDL', 'securityId': '1', 'totalQty': 40, 'availableQty': 40, 'avgCostPrice': 1004}]
        positions = [{'tradingSymbol': 'KNRCON', 'securityId': '2', 'netQty': 167, 'costPrice': 111.28, 'productType': 'CNC', 'exchangeSegment': 'NSE_EQ'},
                     {'tradingSymbol': 'INTRADAY', 'netQty': 5, 'productType': 'INTRADAY', 'exchangeSegment': 'NSE_EQ'}]
        with patch.object(server, 'dhan_read', side_effect=[holdings, positions]) as read:
            result = session.portfolio()
            self.assertEqual(result['holdings'][0]['quantity'], 40)
            self.assertEqual(result['positions'][0]['quantity'], 167)
            self.assertEqual(len(result['positions']), 1)
            session.portfolio()
            self.assertEqual(read.call_count, 2, 'Use a short read-only portfolio cache')
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_connect_status_disconnect_never_exposes_token(self):
        session = server.DhanSession()
        with patch.object(server, 'dhan_profile', return_value=PROFILE):
            status = session.connect(PROFILE['dhanClientId'], TOKEN)
        self.assertTrue(status['connected'])
        self.assertNotIn(TOKEN, json.dumps(status))
        self.assertEqual(status['executionMode'], 'paper')
        self.assertFalse(session.disconnect()['hasSession'])
        self.assertIsNone(session.token)

    def test_wrong_account_does_not_replace_session(self):
        session = server.DhanSession()
        with patch.object(server, 'dhan_profile', return_value=PROFILE):
            session.connect(PROFILE['dhanClientId'], TOKEN)
            with self.assertRaises(server.ConnectionError):
                session.connect('999', TOKEN + 'replacement')
        self.assertEqual(session.token, TOKEN)

    def test_failed_health_check_is_not_connected(self):
        session = server.DhanSession()
        with patch.object(server, 'dhan_profile', return_value=PROFILE):
            session.connect(PROFILE['dhanClientId'], TOKEN)
        with patch.object(server, 'dhan_profile', side_effect=server.ConnectionError('Expired token')):
            status = session.refresh()
        self.assertFalse(status['connected'])
        self.assertEqual(status['error'], 'Expired token')
        with patch.object(server, 'dhan_profile', return_value=PROFILE):
            self.assertTrue(session.refresh()['connected'])

    def test_invalid_input_never_calls_dhan(self):
        session = server.DhanSession()
        with patch.object(server, 'dhan_profile') as api:
            for client_id, token in [('abc', TOKEN), ('123', 'short'), ('123', TOKEN+'\n')]:
                with self.assertRaises(server.ConnectionError):
                    session.connect(client_id, token)
            api.assert_not_called()


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session_patch = patch.object(server, 'DHAN', server.DhanSession())
        cls.session_patch.start()
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.base = 'http://127.0.0.1:' + str(cls.http.server_port)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.session_patch.stop()

    def setUp(self):
        server.DHAN.disconnect()

    def post(self, path, data, headers=None):
        request = Request(self.base+path, data=json.dumps(data).encode(),
                          headers=headers or {'Content-Type': 'application/json'}, method='POST')
        with urlopen(request) as response:
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            return json.load(response)

    def test_connection_endpoints_and_no_order_route(self):
        with patch.object(server, 'dhan_profile', return_value=PROFILE):
            status = self.post('/api/dhan/connect', {'clientId': PROFILE['dhanClientId'], 'accessToken': TOKEN})
            self.assertTrue(status['connected'])
            status = json.load(urlopen(self.base+'/api/dhan/status'))
            self.assertNotIn(TOKEN, json.dumps(status))
            self.assertTrue(self.post('/api/dhan/refresh', {})['connected'])
            self.assertFalse(self.post('/api/dhan/disconnect', {})['hasSession'])
        with self.assertRaises(HTTPError) as error:
            self.post('/api/dhan/order', {})
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_dhan_trade_history_endpoint_is_read_only(self):
        response={'symbol':'CUMMINSIND','orders':[{'side':'BUY','orderId':'buy-1','quantity':10,'averagePrice':3999.94,
                    'firstTradeAt':'2026-10-10 09:20:01','lastTradeAt':'2026-10-10 09:20:05'}]}
        with patch.object(server.DHAN,'trade_history',return_value=response) as history:
            result=self.post('/api/dhan/trade-history',{'symbol':'CUMMINSIND','fromDate':'2026-10-01','toDate':'2026-10-31'})
        self.assertEqual(result,response)
        history.assert_called_once_with('CUMMINSIND','2026-10-01','2026-10-31')
        with self.assertRaises(HTTPError) as error:
            self.post('/api/dhan/order',{'symbol':'CUMMINSIND'})
        self.assertEqual(error.exception.code,404);error.exception.close()

    def test_cross_origin_and_non_json_rejected(self):
        for headers, code in [({'Content-Type': 'application/json', 'Origin': 'https://untrusted.example'}, 403),
                              ({'Content-Type': 'text/plain'}, 415),
                              ({'Content-Type': 'application/json', 'Host': 'untrusted.example'}, 403)]:
            with self.assertRaises(HTTPError) as error:
                self.post('/api/dhan/connect', {}, headers)
            self.assertEqual(error.exception.code, code)
            error.exception.close()

    def test_error_does_not_echo_credentials(self):
        with patch.object(server, 'dhan_profile', side_effect=server.ConnectionError('Dhan rejected the token.')):
            with self.assertRaises(HTTPError) as error:
                self.post('/api/dhan/connect', {'clientId': PROFILE['dhanClientId'], 'accessToken': TOKEN})
            payload = error.exception.read().decode()
            error.exception.close()
        self.assertNotIn(TOKEN, payload)
        self.assertIn('Dhan rejected', payload)

    def test_chart_lab_endpoints_and_private_source(self):
        with patch('server.chart_ai.status', return_value={'connected': False, 'models': [], 'message': 'Start Ollama'}):
            status = json.load(urlopen(self.base+'/api/chart-ai/status'))
        self.assertFalse(status['connected'])
        with patch('server.chart_ai.analyze', return_value={'analysis': {'usable': False}, 'localOnly': True}) as analyze:
            result = self.post('/api/chart-ai/analyze', {'model': 'local-vision', 'image': 'mock'})
            self.assertTrue(result['localOnly'])
            analyze.assert_called_once()
        with urlopen(self.base+'/chart-lab.js') as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base+'/chart_ai.py')
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_initial_page_includes_holdings_without_exposing_token(self):
        import re
        snapshot = {'clientId': '123', 'checkedAt': '2026-11-15T15:00:00Z',
                    'holdings': [{'symbol': 'PAGEIND', 'securityId': '14413', 'quantity': 4, 'averagePrice': 32125}], 'positions': []}
        with patch.object(server.DHAN, 'portfolio', return_value=snapshot):
            with urlopen(self.base+'/?holding=PAGEIND') as response:
                html = response.read().decode('utf-8')
        match = re.search(r'<script id="initialDhanPortfolio" type="application/json">(.*?)</script>', html)
        self.assertIsNotNone(match)
        self.assertEqual(json.loads(match.group(1))['holdings'][0]['quantity'], 4)
        self.assertNotIn(TOKEN, html)
        with patch.object(server.DHAN, 'portfolio', return_value=snapshot):
            with urlopen(self.base+'/holdings') as response:
                holding_html = response.read().decode('utf-8')
        self.assertIn('data-holding-symbol="PAGEIND"', holding_html)
        self.assertIn('<td><b>4</b></td>', holding_html)
        self.assertIn('<section id="live">', holding_html)
        self.assertNotIn('> Ongoing Trades', holding_html)


if __name__ == '__main__':
    unittest.main()
