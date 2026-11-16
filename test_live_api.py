import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
import server
from live_engine import LiveEngine
from test_live_engine import FakeBroker, PLAN, SETTINGS


class LiveAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.broker = FakeBroker()
        self.engine = LiveEngine(Path(self.temp.name)/'live.sqlite', self.broker)
        self.patch = patch.object(server, 'LIVE', self.engine)
        self.patch.start()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.base = 'http://127.0.0.1:'+str(self.http.server_port)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.patch.stop()
        self.engine.db.close()
        self.temp.cleanup()

    def post(self, path, body, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        with urlopen(Request(self.base+path, data=json.dumps(body).encode(), headers=headers, method='POST')) as response:
            return json.load(response)

    def test_explicit_enable_review_arm_and_pause_routes(self):
        with self.assertRaises(HTTPError) as error:
            self.post('/api/live/enable', {})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        self.assertFalse(self.engine.enabled)
        self.post('/api/live/enable', {'acknowledgeRealOrders': True})
        review = self.post('/api/live/preview', {'plan': PLAN, 'settings': SETTINGS, 'triggerPercent': 1})
        result = self.post('/api/live/arm', {'previewId': review['previewId']})
        self.assertEqual(result['state'], 'ARMED')
        self.assertFalse(self.broker.sent, 'Arming API does not bypass the monitored entry condition')
        self.post('/api/live/pause', {})
        self.assertFalse(self.engine.enabled)
        self.assertEqual(self.engine.records('123')[0]['state'], 'ARMED')

    def test_cross_origin_live_write_and_ledger_download_are_blocked(self):
        with self.assertRaises(HTTPError) as error:
            self.post('/api/live/enable', {'acknowledgeRealOrders': True}, 'https://untrusted.example')
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        for path in ('/runtime/live.sqlite', '/runtime/', '/server.py', '/%72untime/live.sqlite'):
            with self.assertRaises(HTTPError) as error:
                urlopen(self.base+path)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        self.assertFalse(self.broker.sent)


if __name__ == '__main__':
    unittest.main()
