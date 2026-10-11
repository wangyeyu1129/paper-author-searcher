import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from paper_author_searcher.providers import HttpClient, SourceError


class Response:
    def __init__(self, body, content_type, status=200):
        self.body, self.status = body, status
        self.headers = {'Content-Type': content_type}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, size):
        return self.body[:size]
    def geturl(self):
        return 'https://dblp.org/search/author/api?q=secret'


class HttpTests(unittest.TestCase):
    def test_html_challenge_diagnostic(self):
        with patch('paper_author_searcher.http.urlopen', return_value=Response(b'<!doctype html><html>bot challenge</html>', 'text/html')):
            with self.assertRaisesRegex(SourceError, 'HTTP 200 Content-Type text/html.*HTML anti-bot'):
                HttpClient().get('https://dblp.org/search/author/api')

    def test_malformed_and_empty_json(self):
        for body, message in [(b'', 'empty response'), (b'{', 'malformed JSON'), (b'[]', 'expected JSON object')]:
            with patch('paper_author_searcher.http.urlopen', return_value=Response(body, 'application/json')):
                with self.assertRaisesRegex(SourceError, message):
                    HttpClient().get('https://dblp.org/search/author/api')

    def test_json_accept_header(self):
        with patch('paper_author_searcher.http.urlopen', return_value=Response(b'{"result":{}}', 'application/json')) as opener:
            self.assertEqual(HttpClient().get('https://dblp.org/search/author/api'), {'result': {}})
            self.assertEqual(opener.call_args[0][0].get_header('Accept'), 'application/json')

    def test_http_forbidden(self):
        with patch('paper_author_searcher.http.urlopen', side_effect=HTTPError('https://dblp.org', 403, 'forbidden', {}, None)):
            with self.assertRaisesRegex(SourceError, 'HTTP 403'):
                HttpClient().get('https://dblp.org/search/author/api')
