import unittest
import io
from urllib.error import HTTPError
from unittest.mock import patch
import server
from dhan_data import InstrumentDirectory, executed_sale


def fill(trade_id='t1', qty=2, price=100):
    return {'dhanClientId': '123', 'orderId': 'SELL1', 'securityId': '18143',
            'transactionType': 'SELL', 'exchangeSegment': 'NSE_EQ', 'productType': 'CNC',
            'exchangeTradeId': trade_id, 'tradedQuantity': qty, 'tradedPrice': price}


class MarketTests(unittest.TestCase):
    def test_scrip_master_tick_size_is_converted_from_paise_to_rupees(self):
        from unittest.mock import MagicMock
        csv = b'SEM_EXM_EXCH_ID,SEM_INSTRUMENT_NAME,SEM_SERIES,SEM_TRADING_SYMBOL,SEM_SMST_SECURITY_ID,SEM_TICK_SIZE\nNSE,EQUITY,EQ,PAGEIND,14413,500\nNSE,EQUITY,EQ,JIOFIN,18143,5\n'
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = io.BytesIO(csv)
        with patch('dhan_data.build_opener', return_value=opener):
            instruments = InstrumentDirectory()
            self.assertEqual(instruments.details('PAGEIND')['tickSize'], 5)
            self.assertEqual(instruments.details('JIOFIN')['tickSize'], .05)

    def session(self):
        session = server.DhanSession()
        session.token = 'fake-token-test-only'
        session.profile = {'dhanClientId': '123'}
        return session

    def test_instrument_resolution_excludes_wrong_exchange_and_ambiguity(self):
        row = {'SEM_EXM_EXCH_ID': 'NSE', 'SEM_INSTRUMENT_NAME': 'EQUITY',
               'SEM_SERIES': 'EQ', 'SEM_TRADING_SYMBOL': 'JIOFIN', 'SEM_SMST_SECURITY_ID': '18143'}
        self.assertEqual(InstrumentDirectory.parse([row]), {'JIOFIN': 18143})
        self.assertEqual(InstrumentDirectory.parse([row, {**row, 'SEM_SMST_SECURITY_ID': '999'}]), {})
        self.assertEqual(InstrumentDirectory.parse([{**row, 'SEM_EXM_EXCH_ID': 'BSE'}]), {})

    def test_batch_quotes_missing_prices_and_short_cache(self):
        session = self.session()
        response = {'status': 'success', 'data': {'NSE_EQ': {'18143': {'last_price': 250.5}}}}
        with patch.object(server.INSTRUMENTS, 'resolve', return_value={'JIOFIN': 18143, 'TCS': 11536}), \
                patch.object(server, 'dhan_read', return_value=response) as api:
            quotes = session.quotes(['JIOFIN', 'TCS', 'UNKNOWN'])
            self.assertEqual(quotes['quotes']['JIOFIN']['price'], 250.5)
            self.assertEqual(quotes['unresolved'], ['UNKNOWN'])
            self.assertEqual(quotes['missing'], ['TCS'])
            self.assertEqual(quotes['quoteDiagnostics']['requestedInstrumentCount'], 2)
            self.assertEqual(quotes['quoteDiagnostics']['returnedInstrumentCount'], 1)
            self.assertEqual(quotes['quoteDiagnostics']['apiStatus'], 'success')
            self.assertNotIn('fake-token', str(quotes))
            self.assertEqual(session.quotes(['UNKNOWN', 'JIOFIN', 'TCS']), quotes)
            api.assert_called_once()
            self.assertEqual(api.call_args.args[2], '/marketfeed/ltp')
            self.assertEqual(api.call_args.args[3], {'NSE_EQ': [11536, 18143]})

    def test_empty_quote_set_reports_dhan_response_diagnostics(self):
        session = self.session()
        response = {'status':'success', 'data':{'NSE_EQ':{}}}
        with patch.object(server.INSTRUMENTS, 'resolve', return_value={'JIOFIN':18143}), \
                patch.object(server, 'dhan_read', return_value=response):
            result = session.quotes(['JIOFIN'])
        self.assertEqual(result['quotes'], {})
        self.assertEqual(result['missing'], ['JIOFIN'])
        self.assertEqual(result['quoteDiagnostics'], {'apiStatus':'success','requestedInstrumentCount':1,
                         'returnedInstrumentCount':0,'returnedSegmentNames':['NSE_EQ']})

    def test_quote_rejects_disconnected_and_bad_symbols(self):
        with self.assertRaises(server.ConnectionError):
            server.DhanSession().quotes(['JIOFIN'])
        for symbols in [[], ['../profile'], ['JIOFIN'] * 101, 'JIOFIN']:
            with self.assertRaises(ValueError):
                self.session().quotes(symbols)

    def test_inactive_data_plan_does_not_disconnect_trading_session(self):
        session = self.session()
        session.profile['dataPlan'] = 'Deactive'
        with patch.object(server, 'dhan_read') as api:
            with self.assertRaisesRegex(server.ConnectionError, 'inactive Data API plan'):
                session.quotes(['BDL'])
            api.assert_not_called()
        self.assertTrue(session.status()['connected'])

    def test_quote_errors_do_not_show_sell_order_guidance(self):
        session = self.session()
        with patch.object(server.INSTRUMENTS, 'resolve', return_value={'JIOFIN': 18143}), \
                patch.object(server, 'dhan_read', side_effect=server.ConnectionError('Dhan market-data service is temporarily unavailable (HTTP 502). Retry CMP refresh shortly; this is not a sell-order-ID issue.')):
            with self.assertRaisesRegex(server.ConnectionError, 'market-data service.*HTTP 502') as caught:
                session.quotes(['JIOFIN'])
            self.assertNotIn('sell order ID', str(caught.exception))

    def test_dhan_read_maps_quote_gateway_failure_to_retryable_message(self):
        error = HTTPError('https://api.dhan.co/v2/marketfeed/ltp', 502, 'Bad Gateway', {}, io.BytesIO())
        with patch('server.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaisesRegex(server.ConnectionError, 'market-data service.*HTTP 502') as caught:
                server.dhan_read('token', '123', '/marketfeed/ltp', {'NSE_EQ': [18143]})
        self.assertNotIn('sell order ID', str(caught.exception))

    def test_weighted_executed_sale(self):
        result = executed_sale([fill(qty=2, price=100), fill('t2', 3, 110)], 'SELL1', 18143, '123', 5)
        self.assertEqual(result['salePrice'], 106)
        self.assertEqual(result['quantity'], 5)

    def test_sale_rejects_wrong_account_side_product_instrument_and_duplicates(self):
        bad_rows = [{**fill(), 'transactionType': 'BUY'}, {**fill(), 'dhanClientId': '999'},
                    {**fill(), 'productType': 'INTRADAY'}, {**fill(), 'securityId': '9'},
                    {**fill(), 'orderId': 'WRONG'}, {**fill(), 'tradedPrice': 'NaN'},
                    {**fill(), 'tradedPrice': 'bad'}]
        for row in bad_rows:
            with self.assertRaises(ValueError):
                executed_sale([row], 'SELL1', 18143, '123', 2)
        with self.assertRaises(ValueError):
            executed_sale([fill(), fill()], 'SELL1', 18143, '123', 4)
        with self.assertRaisesRegex(ValueError, 'Partial exits'):
            executed_sale([fill()], 'SELL1', 18143, '123', 5)

    def test_sale_read_is_bound_to_order_and_recorded_quantity(self):
        with patch.object(server.INSTRUMENTS, 'resolve', return_value={'JIOFIN': 18143}), \
                patch.object(server, 'dhan_read', return_value=[fill()]) as api:
            result = self.session().sale('JIOFIN', 'SELL1', 2)
            self.assertEqual(result['salePrice'], 100)
            self.assertEqual(api.call_args.args[2], '/trades/SELL1')
        with self.assertRaises(server.ConnectionError):
            self.session().sale('JIOFIN', '../orders', 2)


if __name__ == '__main__':
    unittest.main()
