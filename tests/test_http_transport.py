import errno
import http.client
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

from paper_author_searcher.http import HttpClient
from paper_author_searcher.http_transport import KeepAliveTransport


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'  # keep-alive
    seen: ClassVar[list[tuple]] = []
    other_port = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        Handler.seen.append((self.path, self.headers.get('X-Api-Key'), self.client_address[1]))
        if self.path.startswith('/json'):
            self._send(200, json.dumps({'path': self.path}).encode(), 'application/json')
        elif self.path == '/missing':
            self._send(404, b'{"error": "nope"}', 'application/json')
        elif self.path == '/redirect':
            self._redirect('/json?after=redirect')
        elif self.path == '/away':
            self._redirect(f'http://127.0.0.1:{Handler.other_port}/json?after=away')
        elif self.path == '/close':
            self._send(200, b'{"closing": true}', 'application/json', close=True)
        elif self.path == '/large':
            self._send(200, b'{"padding": "' + b'x' * 50000 + b'"}', 'application/json')

    def _redirect(self, location):
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _send(self, status, body, content_type, close=False):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        if close:
            self.send_header('Connection', 'close')
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # clients that drop a connection mid-body are part of the tests


class ScriptedConnection:
    """A connection whose request raises the given error; without one, it answers."""

    def __init__(self, error=None, socket_error=None):
        self.error = error
        self.sock = Mock()
        self.sock.settimeout.side_effect = socket_error
        self.requests = 0

    def request(self, *args):
        self.requests += 1
        if self.error:
            raise self.error

    def getresponse(self):
        return Mock(status=200, getheader=Mock(return_value=None))

    def close(self):
        pass


class BrokenConnectionTests(unittest.TestCase):
    def transport(self, *fresh):
        transport = KeepAliveTransport(proxies={})
        transport._connect = Mock(side_effect=fresh)
        return transport

    def get(self, transport):
        return transport.open(Request('http://source.test/x'), timeout=1)

    def test_a_retry_that_fails_leaves_no_broken_connection_for_the_next_request(self):
        healthy = ScriptedConnection()
        transport = self.transport(
            ScriptedConnection(),
            ScriptedConnection(http.client.RemoteDisconnected()),
            ScriptedConnection(TimeoutError('handshake timed out')),
            healthy,
        )
        self.get(transport)
        transport._pool()[('http', 'source.test', 80)] = transport._connect()
        with self.assertRaises(TimeoutError):
            self.get(transport)
        self.get(transport)
        self.assertEqual(healthy.requests, 1)

    def test_a_pooled_connection_with_a_dead_socket_is_replaced(self):
        healthy = ScriptedConnection()
        transport = self.transport(
            ScriptedConnection(socket_error=OSError(errno.EBADF, 'Bad file descriptor')), healthy
        )
        with self.assertRaises(OSError):
            self.get(transport)
        self.get(transport)
        self.assertEqual(healthy.requests, 1)


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers = [QuietServer(('127.0.0.1', 0), Handler) for _ in range(2)]
        for server in cls.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.servers[0].server_address[1]}'
        Handler.other_port = cls.servers[1].server_address[1]

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.shutdown()
            server.server_close()

    def setUp(self):
        Handler.seen = []
        self.transport = KeepAliveTransport(proxies={})
        self.addCleanup(self.transport.close)

    def fetch(self, path, headers=None):
        with self.transport.open(Request(self.base + path, headers=headers or {}), timeout=5) as response:
            return response.status, response.read(), response.geturl()

    def test_sequential_requests_reuse_one_connection(self):
        for index in range(5):
            status, body, _ = self.fetch(f'/json?page={index}')
            self.assertEqual((status, json.loads(body)['path']), (200, f'/json?page={index}'))
        self.assertEqual(self.transport.statistics['connections_opened'], 1)
        self.assertEqual(self.transport.statistics['connections_reused'], 4)
        self.assertEqual(len({port for _, _, port in Handler.seen}), 1)

    def test_error_status_raises_http_error_with_body_and_keeps_connection(self):
        with self.assertRaises(HTTPError) as raised:
            self.fetch('/missing')
        self.assertEqual(raised.exception.code, 404)
        self.assertIn(b'nope', raised.exception.read())
        self.fetch('/json')
        self.assertEqual(self.transport.statistics['connections_opened'], 1)

    def test_same_host_redirect_is_followed(self):
        status, body, url = self.fetch('/redirect')
        self.assertEqual(status, 200)
        self.assertTrue(url.endswith('/json?after=redirect'))
        self.assertEqual(json.loads(body)['path'], '/json?after=redirect')

    def test_cross_host_redirect_drops_credentials(self):
        self.fetch('/away', {'X-Api-Key': 'secret'})
        self.assertEqual(Handler.seen[0][1], 'secret')
        self.assertIsNone(Handler.seen[1][1])

    def test_server_closed_connection_is_reopened(self):
        self.fetch('/close')
        status, _, _ = self.fetch('/json')
        self.assertEqual(status, 200)
        self.assertEqual(len({port for _, _, port in Handler.seen}), 2)

    def test_partially_read_body_discards_the_connection(self):
        with self.transport.open(Request(self.base + '/large'), timeout=5) as response:
            response.read(10)
        self.fetch('/json')
        self.assertEqual(self.transport.statistics['connections_opened'], 2)

    def test_each_thread_has_its_own_connection(self):
        threads = [threading.Thread(target=self.fetch, args=('/json',)) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.transport.statistics['connections_opened'], 3)

    def test_configured_proxy_falls_back_to_urllib(self):
        transport = KeepAliveTransport(proxies={'http': 'http://proxy.invalid:3128'})
        with (
            patch('paper_author_searcher.http_transport.proxy_bypass', return_value=False),
            patch('paper_author_searcher.http_transport.urlopen', return_value='proxied') as opener,
        ):
            self.assertEqual(transport.open(Request(self.base + '/json'), timeout=1), 'proxied')
        opener.assert_called_once()

    def test_http_client_pages_over_one_connection(self):
        client = HttpClient(timeout=5, retries=0, transport=self.transport)
        for page in range(3):
            self.assertEqual(client.get(self.base + '/json', {'page': page})['path'], f'/json?page={page}')
        self.assertEqual(self.transport.statistics['connections_opened'], 1)


if __name__ == '__main__':
    unittest.main()
