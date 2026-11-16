"""Keep an existing localhost origin working while using a single app backend."""
import argparse
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class CompatibilityHandler(BaseHTTPRequestHandler):
    server_version = 'DeepTradingDeskCompatibility/1'

    def error(self, status, message):
        body = json.dumps({'error': message}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def forward(self):
        host = self.headers.get('Host', '')
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if host not in allowed or self.headers.get('Origin') not in (None, 'http://' + host):
            self.error(403, 'Only same-origin localhost requests are allowed.')
            return
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or not self.path.startswith('/') or self.path.startswith('//'):
            self.error(400, 'Invalid application path.')
            return
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 <= size <= 8_000_000 or self.headers.get('Transfer-Encoding'):
                raise ValueError()
        except ValueError:
            self.error(413, 'Request is too large or has an unsupported transfer format.')
            return
        body = self.rfile.read(size) if size else None
        target = f'127.0.0.1:{self.server.upstream_port}'
        headers = {key: value for key, value in self.headers.items() if key.lower() in ('accept', 'accept-language', 'content-type', 'user-agent', 'if-modified-since')}
        headers['Host'] = target
        if self.headers.get('Origin'):
            headers['Origin'] = 'http://' + target
        response_started=False
        connection = http.client.HTTPConnection('127.0.0.1', self.server.upstream_port, timeout=75)
        try:
            # Forward exactly once. An uncertain trading request is never retried here.
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(12_000_001)
            if len(data) > 12_000_000:
                raise ValueError('Response too large')
            response_started=True
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in ('connection', 'transfer-encoding', 'content-length', 'keep-alive', 'server', 'date'):
                    self.send_header(key, value)
            self.send_header('Content-Length', str(len(data)) if self.command != 'HEAD' else response.getheader('Content-Length', '0'))
            self.send_header('X-Deep-Trading-Desk', 'compatibility-proxy')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)
        except (OSError, http.client.HTTPException, ValueError):
            if response_started:
                self.close_connection=True
            else:
                self.error(502, 'The main Deep Trading Desk backend is unavailable. Restart the application launcher. Do not repeat an uncertain live order manually; check its status first.')
        finally:
            connection.close()

    def log_message(self, format, *args):
        # Do not write session-related request metadata to the proxy console.
        pass

    do_GET = forward
    do_HEAD = forward
    do_POST = forward


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Preserve a local application origin without starting another trading engine')
    parser.add_argument('--port', type=int, default=8081)
    parser.add_argument('--upstream', type=int, default=8080)
    args = parser.parse_args()
    if args.port == args.upstream:
        parser.error('Compatibility and backend ports must differ.')
    proxy_server = ThreadingHTTPServer(('127.0.0.1', args.port), CompatibilityHandler)
    proxy_server.upstream_port = args.upstream
    print(f'Deep Trading Desk compatibility: 127.0.0.1:{args.port} -> 127.0.0.1:{args.upstream}', flush=True)
    proxy_server.serve_forever()
