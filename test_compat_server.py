import http.client
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from compat_server import CompatibilityHandler


class Upstream(BaseHTTPRequestHandler):
    calls = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.calls.append((self.path, body, self.headers.get('Origin')))
        result = json.dumps({'received': self.path}).encode()
        self.send_response(422)
        self.send_header('Content-Length', str(len(result)))
        self.end_headers()
        self.wfile.write(result)

    def log_message(self, *args):
        pass


class CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        cls.proxy = ThreadingHTTPServer(('127.0.0.1', 0), CompatibilityHandler)
        cls.proxy.upstream_port = cls.upstream.server_port
        for server in (cls.upstream, cls.proxy):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for server in (cls.proxy, cls.upstream):
            server.shutdown()
            server.server_close()

    def request(self, origin):
        connection = http.client.HTTPConnection('127.0.0.1', self.proxy.server_port)
        connection.request('POST', '/api/test?check=2', b'{"test":true}', {'Origin': origin, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        result = response.status, response.read(), response.getheader('X-Deep-Trading-Desk')
        connection.close()
        return result

    def test_forward_once_preserves_body_path_and_error_status(self):
        Upstream.calls.clear()
        status, body, marker = self.request(f'http://127.0.0.1:{self.proxy.server_port}')
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(body)['received'], '/api/test?check=2')
        self.assertEqual(marker, 'compatibility-proxy')
        self.assertEqual(Upstream.calls, [('/api/test?check=2', b'{"test":true}', f'http://127.0.0.1:{self.upstream.server_port}')])

    def test_cross_origin_request_never_reaches_upstream(self):
        Upstream.calls.clear()
        self.assertEqual(self.request('https://example.com')[0], 403)
        self.assertEqual(Upstream.calls, [])


if __name__ == '__main__':
    unittest.main()
