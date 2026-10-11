import io
import socket
import ssl
import tempfile
import unittest
from dataclasses import replace
from http.client import HTTPSConnection
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from paper_author_searcher.config import DEFAULT_DOWNLOAD
from paper_author_searcher.http import HttpClient, SourceError
from paper_author_searcher.network_safety import (
    PublicHTTPSHandler, is_public_address, pinned_connection, public_addresses, secure_download_opener,
)


URL = 'https://repository.example.edu/document'


class Response:
    def __init__(self, body, headers=None, status=200):
        self.stream = io.BytesIO(body)
        self.status = status
        self.headers = headers or {'Content-Type': 'application/pdf', 'Content-Length': str(len(body))}
        self.read_sizes = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self, size):
        self.read_sizes.append(size)
        return self.stream.read(size)

    def geturl(self):
        return URL


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.destination = Path(self.directory.name) / 'download.part'

    def transfer(self, response, config=DEFAULT_DOWNLOAD):
        opener = Mock()
        opener.open.return_value = response
        with patch('paper_author_searcher.http.secure_download_opener', return_value=opener):
            receipt = HttpClient(retries=0).stream_pdf(URL, self.destination, config)
        return receipt, opener

    def test_streaming_hash_size_and_request(self):
        import hashlib
        body = b'%PDF-1.7\n' + b'x' * 300000
        response = Response(body)
        receipt, opener = self.transfer(response)
        self.assertEqual(receipt.file_size, len(body))
        self.assertEqual(receipt.sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(self.destination.read_bytes(), body)
        self.assertLessEqual(max(response.read_sizes), DEFAULT_DOWNLOAD.chunk_bytes)
        self.assertGreater(len(response.read_sizes), 2)
        self.assertTrue(response.closed)
        request = opener.open.call_args[0][0]
        self.assertIsNone(request.get_header('Range'))
        self.assertEqual(request.get_header('Accept-encoding'), 'identity')

    def test_declared_size_limit_rejects_before_reading(self):
        response = Response(b'%PDF-', {'Content-Type': 'application/pdf', 'Content-Length': '4096'})
        with self.assertRaisesRegex(SourceError, 'Declared download size'):
            self.transfer(response, replace(DEFAULT_DOWNLOAD, maximum_file_bytes=1024))
        self.assertEqual(response.read_sizes, [])

    def test_unknown_size_is_bounded(self):
        response = Response(b'%PDF-' + b'x' * 3000, {'Content-Type': 'application/pdf'})
        with self.assertRaisesRegex(SourceError, 'exceeds'):
            self.transfer(response, replace(DEFAULT_DOWNLOAD, maximum_file_bytes=1024))
        self.assertEqual(response.read_sizes, [1025])

    def test_truncated_response_detected(self):
        response = Response(b'%PDF-1.7', {'Content-Type': 'application/pdf', 'Content-Length': '999'})
        with self.assertRaisesRegex(SourceError, 'Downloaded size'):
            self.transfer(response)

    def test_html_and_fake_pdf_body_not_accepted(self):
        for response in (Response(b'<html>login</html>', {'Content-Type': 'text/html'}),
                         Response(b'<html>error</html>', {'Content-Type': 'application/pdf'})):
            with self.assertRaises(SourceError):
                self.transfer(response)

    def test_unexpected_partial_response_refused(self):
        headers = {'Content-Type': 'application/pdf', 'Content-Range': 'bytes 0-1023/9999'}
        with self.assertRaisesRegex(SourceError, 'partial download'):
            self.transfer(Response(b'%PDF-', headers, status=206))
        response = Response(b'%PDF-1.7', {'Content-Type': 'application/pdf', 'Content-Range': 'bytes 0-7/8'}, 206)
        self.assertEqual(self.transfer(response)[0].file_size, 8)

    def test_compressed_transport_refused(self):
        with self.assertRaisesRegex(SourceError, 'compressed'):
            self.transfer(Response(b'%PDF-', {'Content-Type': 'application/pdf', 'Content-Encoding': 'gzip'}))

    def test_rate_limit_retries_then_downloads(self):
        opener = Mock()
        opener.open.side_effect = [HTTPError(URL, 429, 'limited', {'Retry-After': '999'}, None), Response(b'%PDF-1.7')]
        with patch('paper_author_searcher.http.secure_download_opener', return_value=opener), \
                patch('paper_author_searcher.http.time.sleep') as sleep:
            receipt = HttpClient(retries=1).stream_pdf(URL, self.destination, DEFAULT_DOWNLOAD)
        self.assertEqual(receipt.file_size, 8)
        self.assertEqual(sleep.call_args[0][0], 5)

    def test_network_error_retry_restarts_temporary_file(self):
        opener = Mock()
        interrupted = Response(b'%PDF-1.7' + b'x' * 1000)
        interrupted.read = Mock(side_effect=[b'%PDF-incomplete', URLError('interrupted')])
        opener.open.side_effect = [interrupted, Response(b'%PDF-1.7')]
        with patch('paper_author_searcher.http.secure_download_opener', return_value=opener), \
                patch('paper_author_searcher.http.time.sleep'):
            receipt = HttpClient(retries=1).stream_pdf(URL, self.destination, DEFAULT_DOWNLOAD)
        self.assertEqual(receipt.file_size, 8)
        self.assertEqual(self.destination.read_bytes(), b'%PDF-1.7')

    def test_forbidden_is_not_retried(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(URL, 403, 'forbidden', {}, None)
        with patch('paper_author_searcher.http.secure_download_opener', return_value=opener):
            with self.assertRaises(SourceError):
                HttpClient(retries=2).stream_pdf(URL, self.destination, DEFAULT_DOWNLOAD)
        self.assertEqual(opener.open.call_count, 1)

    def test_small_stream_blocks_do_not_mistake_split_signature_for_error(self):
        response = Response(b'')
        response.headers = {'Content-Type': 'application/pdf'}
        response.read1 = Mock(side_effect=[b'%', b'P', b'D', b'F', b'-', b'1.7', b''])
        self.assertEqual(self.transfer(response)[0].file_size, 8)

    def test_slow_transfer_total_deadline_is_enforced(self):
        with patch('paper_author_searcher.http.time.monotonic', side_effect=[0, 301]):
            with self.assertRaisesRegex(SourceError, 'transfer deadline'):
                self.transfer(Response(b'%PDF-1.7'))


class URLSafetyTests(unittest.TestCase):
    def test_private_loopback_link_local_and_multicast_addresses_blocked(self):
        addresses = ('127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.1.1', '169.254.169.254',
                     '100.64.0.1', '0.0.0.0', '224.0.0.1', '::1', 'fc00::1', 'fe80::1',
                     '::ffff:127.0.0.1', '2002:0a00:0001::1')
        for address in addresses:
            self.assertFalse(is_public_address(address), address)
        self.assertTrue(is_public_address('1.1.1.1'))

    def test_public_hostname_resolving_private_address_refused(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))]
        with patch('paper_author_searcher.network_safety.socket.getaddrinfo', return_value=answers):
            with self.assertRaisesRegex(ValueError, 'nonpublic'):
                public_addresses(URL, (80, 443))

    def test_mixed_public_and_private_dns_answers_refused(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))
                   for address in ('1.1.1.1', '127.0.0.1')]
        with patch('paper_author_searcher.network_safety.socket.getaddrinfo', return_value=answers):
            with self.assertRaises(ValueError):
                public_addresses(URL, (80, 443))

    def test_connection_pins_address_preserving_tls_hostname(self):
        factory = pinned_connection(HTTPSConnection, ['1.1.1.1'])
        connection = factory('repository.example.edu', timeout=5)
        self.assertEqual(connection.host, 'repository.example.edu')
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        with patch('paper_author_searcher.network_safety.socket.create_connection') as connect:
            connection._create_connection(('repository.example.edu', 443), 5)
        self.assertEqual(connect.call_args[0][0], ('1.1.1.1', 443))

    def test_each_https_redirect_target_is_resolved_again(self):
        handler = PublicHTTPSHandler((80, 443))
        request = Mock(full_url='https://redirect.example.edu/document')
        with patch('paper_author_searcher.network_safety.public_addresses', side_effect=ValueError('private')) as resolve:
            with self.assertRaises(ValueError):
                handler.https_open(request)
        resolve.assert_called_once_with(request.full_url, (80, 443))

    def test_environment_proxies_disabled(self):
        from paper_author_searcher.http import PublicRedirectHandler
        opener = secure_download_opener(PublicRedirectHandler(5), (80, 443))
        for handler in opener.handlers:
            if hasattr(handler, 'proxies'):
                self.assertEqual(handler.proxies, {})

    def test_local_schemes_credentials_and_unexpected_ports_rejected(self):
        for url in ('file:///tmp/a.pdf', 'http://localhost/a', 'https://user:secret@example.edu/a'):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(SourceError):
                    HttpClient().stream_pdf(url, Path(directory) / 'temp.part', DEFAULT_DOWNLOAD)
        with self.assertRaises(ValueError):
            public_addresses('https://example.edu:22/file', (80, 443))
