import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request
from http.client import IncompleteRead

from paper_author_searcher.config import PDFConfig
from paper_author_searcher.http import HttpClient, PublicRedirectHandler, SourceError, URLProbe
from paper_author_searcher.models import PDFCandidate
from paper_author_searcher.pdf_validation import PDFURLValidator


URL = 'https://repository.example.edu/paper.pdf'


def candidate(identity='verified'):
    return PDFCandidate(URL, 'repository', identity_status=identity,
                        access_basis='Explicit public metadata PDF link')


class ValidationTests(unittest.TestCase):
    def validate(self, body=b'%PDF-1.7\n', content_type='application/pdf', status=200,
                 identity='verified', headers=None, final_url=URL):
        client = Mock()
        client.probe.return_value = URLProbe(final_url, status, content_type, headers or {}, body)
        item = PDFURLValidator(client).validate(candidate(identity))
        return item

    def test_pdf_signature_and_identity_required(self):
        item = self.validate()
        self.assertEqual(item.validation_status, 'verified')
        self.assertTrue(item.pdf_signature_confirmed)
        self.assertEqual(item.bytes_read, 9)

    def test_mime_type_alone_is_insufficient(self):
        item = self.validate(b'<html>login required</html>')
        self.assertEqual(item.validation_status, 'unverified')
        self.assertFalse(item.pdf_signature_confirmed)

    def test_html_response_invalid_even_with_pdf_like_prefix(self):
        self.assertEqual(self.validate(content_type='text/html').validation_status, 'invalid')

    def test_octet_stream_pdf_can_be_verified(self):
        self.assertEqual(self.validate(content_type='application/octet-stream').validation_status, 'verified')

    def test_accessible_pdf_without_identity_is_unverified(self):
        item = self.validate(identity='unverified')
        self.assertTrue(item.pdf_signature_confirmed)
        self.assertEqual(item.validation_status, 'unverified')

    def test_conflicting_identity_blocks_pdf(self):
        self.assertEqual(self.validate(identity='conflicting').validation_status, 'invalid')

    def test_unknown_public_access_basis_is_not_verified(self):
        client = Mock()
        client.probe.return_value = URLProbe(URL, 200, 'application/pdf', {}, b'%PDF-1.7')
        item = candidate()
        item.access_basis = ''
        self.assertEqual(PDFURLValidator(client).validate(item).validation_status, 'unverified')

    def test_partial_response_must_start_at_zero(self):
        self.assertEqual(self.validate(status=206, headers={'Content-Range': 'bytes 100-108/900'}).validation_status,
                         'unverified')
        self.assertEqual(self.validate(status=206, headers={'Content-Range': 'bytes 0-8/900'}).validation_status,
                         'verified')

    def test_error_statuses_never_count_as_verified(self):
        for status in (403, 404, 429, 500, 503):
            item = self.validate(status=status)
            self.assertEqual(item.validation_status, 'unverified')
            self.assertEqual(item.http_status, status)

    def test_timeout_and_source_errors_retained(self):
        client = Mock()
        client.probe.side_effect = SourceError('HTTP 403', status_code=403)
        item = PDFURLValidator(client).validate(candidate())
        self.assertEqual(item.http_status, 403)
        self.assertEqual(item.access_status, 'unreachable')
        self.assertEqual(item.error_details, ['HTTP 403'])

    def test_redirect_to_different_arxiv_paper_is_blocked(self):
        client = Mock()
        client.probe.return_value = URLProbe('https://arxiv.org/pdf/2401.00002', 200,
                                            'application/pdf', {}, b'%PDF-1.7')
        item = candidate()
        item.url = 'https://arxiv.org/pdf/2401.00001'
        self.assertEqual(PDFURLValidator(client).validate(item).validation_status, 'invalid')

    def test_normal_redirect_recorded(self):
        item = self.validate(final_url='https://cdn.example.edu/object')
        self.assertEqual(item.validation_status, 'verified')
        self.assertEqual(item.final_url, 'https://cdn.example.edu/object')

    def test_pdf_must_not_conflict_with_saved_arxiv_identifier(self):
        client = Mock()
        client.probe.return_value = URLProbe('https://arxiv.org/pdf/2401.00002', 200,
                                            'application/pdf', {}, b'%PDF-1.7')
        item = candidate()
        item.url = client.probe.return_value.url
        paper = {'records': [{'external_ids': {'arxiv': '2401.00001'}}]}
        self.assertEqual(PDFURLValidator(client).validate(item, paper).validation_status, 'invalid')

    def test_different_versions_of_same_arxiv_work_are_retained(self):
        client = Mock()
        client.probe.return_value = URLProbe('https://arxiv.org/pdf/2401.00001v1', 200,
                                            'application/pdf', {}, b'%PDF-1.7')
        item = candidate()
        item.url = client.probe.return_value.url
        paper = {'records': [{'external_ids': {'arxiv': '2401.00001v2'}}]}
        self.assertEqual(PDFURLValidator(client).validate(item, paper).validation_status, 'verified')


class Response:
    def __init__(self, content_type='application/pdf', body=b'%PDF-' + b'x' * 100000):
        self.headers = {'Content-Type': content_type}
        self.status = 200
        self.body = body
        self.read_sizes = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self, size):
        self.read_sizes.append(size)
        return self.body[:size]

    def geturl(self):
        return URL


class ProbeTests(unittest.TestCase):
    def test_ignored_range_still_reads_only_prefix_and_closes(self):
        response = Response()
        opener = Mock()
        opener.open.return_value = response
        with patch('paper_author_searcher.http.secure_download_opener', return_value=opener) as safe_opener:
            probe = HttpClient().probe(URL, maximum_bytes=128)
        self.assertEqual(safe_opener.call_args[0][1], (80, 443))
        self.assertEqual(len(probe.prefix), 128)
        self.assertEqual(response.read_sizes, [128])
        self.assertTrue(response.closed)
        request = opener.open.call_args[0][0]
        self.assertEqual(request.get_header('Range'), 'bytes=0-127')
        self.assertEqual(request.get_header('Accept-encoding'), 'identity')

    def test_html_probe_does_not_expand_pdf_read_limit(self):
        opener = Mock()
        opener.open.return_value = Response()
        with patch('paper_author_searcher.http.secure_download_opener', return_value=opener):
            self.assertEqual(len(HttpClient().probe(URL, 64, html_bytes=1024).prefix), 64)
        opener.open.return_value = Response('text/html', b'x' * 10000)
        with patch('paper_author_searcher.http.secure_download_opener', return_value=opener):
            self.assertEqual(len(HttpClient().probe(URL, 64, html_bytes=1024).prefix), 1024)

    def test_http_rate_limit_retries_bounded(self):
        opener = Mock()
        opener.open.side_effect = [HTTPError(URL, 429, 'limit', {'Retry-After': '3'}, None), Response()]
        with patch('paper_author_searcher.http.secure_download_opener', return_value=opener), \
                patch('paper_author_searcher.http.time.sleep') as sleep:
            self.assertEqual(HttpClient(retries=1).probe(URL).status, 200)
        self.assertEqual(sleep.call_args[0][0], 3)
        self.assertEqual(opener.open.call_count, 2)

    def test_network_errors_retry_then_keep_error(self):
        opener = Mock()
        opener.open.side_effect = URLError('timed out')
        with patch('paper_author_searcher.http.secure_download_opener', return_value=opener), \
                patch('paper_author_searcher.http.time.sleep'):
            with self.assertRaises(SourceError):
                HttpClient(retries=1).probe(URL)
        self.assertEqual(opener.open.call_count, 2)

    def test_403_not_retried(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(URL, 403, 'forbidden', {}, None)
        with patch('paper_author_searcher.http.secure_download_opener', return_value=opener):
            with self.assertRaises(SourceError) as raised:
                HttpClient().probe(URL)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(opener.open.call_count, 1)

    def test_truncated_http_response_becomes_source_error(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.headers = {'Content-Type': 'application/pdf'}
        response.read.side_effect = IncompleteRead(b'%PDF-', 10)
        opener = Mock()
        opener.open.return_value = response
        with patch('paper_author_searcher.http.secure_download_opener', return_value=opener):
            with self.assertRaises(SourceError):
                HttpClient(retries=0).probe(URL)

    def test_nonpublic_urls_and_redirects_refused(self):
        urls = ['file:///tmp/paper.pdf', 'https://localhost/paper', 'http://127.0.0.1/paper',
                'https://user:password@example.edu/file', 'http://[::1]/file']
        for url in urls:
            with self.assertRaises(SourceError):
                HttpClient().probe(url)
        with self.assertRaises(SourceError):
            PublicRedirectHandler(5).redirect_request(Request(URL), None, 302, '', {}, 'http://127.0.0.1/file')

    def test_config_rejects_unbounded_or_impossible_limits(self):
        with self.assertRaises(ValueError):
            PDFConfig(preview_bytes=4)
        with self.assertRaises(ValueError):
            PDFConfig(maximum_redirects=-1)
