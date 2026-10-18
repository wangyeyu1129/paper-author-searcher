import json
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
    def test_post_json_batch_uses_json_body_and_explicit_array_parser(self):
        response = Response(b'[{"paperId":"example"},null]', 'application/json')
        with patch('paper_author_searcher.http.urlopen', return_value=response) as opener:
            result = HttpClient().post_json(
                'https://api.semanticscholar.org/graph/v1/paper/batch',
                {'ids': ['one', 'two']},
                params={'fields': 'openAccessPdf'},
                expected_type=list,
            )
        request = opener.call_args[0][0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data), {'ids': ['one', 'two']})
        self.assertIn('fields=openAccessPdf', request.full_url)
        self.assertEqual(result, [{'paperId': 'example'}, None])

    def test_batch_html_is_an_api_failure_not_an_empty_result(self):
        response = Response(b'<html>challenge</html>', 'text/html')
        with patch('paper_author_searcher.http.urlopen', return_value=response):
            with self.assertRaises(SourceError) as raised:
                HttpClient().post_json(
                    'https://api.semanticscholar.org/graph/v1/paper/batch', {'ids': ['one']}, expected_type=list
                )
        self.assertEqual(raised.exception.error_type, 'invalid_content_type')

    def test_html_challenge_diagnostic(self):
        with patch(
            'paper_author_searcher.http.urlopen',
            return_value=Response(b'<!doctype html><html>bot challenge</html>', 'text/html'),
        ):
            with self.assertRaisesRegex(SourceError, 'HTTP 200 Content-Type text/html.*HTML anti-bot'):
                HttpClient().get('https://dblp.org/search/author/api')

    def test_malformed_and_empty_json(self):
        for body, message in [(b'', 'empty response'), (b'{', 'malformed JSON'), (b'[]', 'expected JSON object')]:
            with patch('paper_author_searcher.http.urlopen', return_value=Response(body, 'application/json')):
                with self.assertRaisesRegex(SourceError, message):
                    HttpClient().get('https://dblp.org/search/author/api')

    def test_json_accept_header(self):
        with patch(
            'paper_author_searcher.http.urlopen', return_value=Response(b'{"result":{}}', 'application/json')
        ) as opener:
            self.assertEqual(HttpClient().get('https://dblp.org/search/author/api'), {'result': {}})
            self.assertEqual(opener.call_args[0][0].get_header('Accept'), 'application/json')

    def test_http_forbidden(self):
        with patch(
            'paper_author_searcher.http.urlopen', side_effect=HTTPError('https://dblp.org', 403, 'forbidden', {}, None)
        ):
            with self.assertRaisesRegex(SourceError, 'HTTP 403'):
                HttpClient().get('https://dblp.org/search/author/api')
