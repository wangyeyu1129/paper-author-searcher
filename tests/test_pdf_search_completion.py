import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import HttpClient, SourceError, URLProbe
from paper_author_searcher.pdf_downloader import PDFDownloader
from paper_author_searcher.pdf_public_pages import PublicPagePDFProvider
from paper_author_searcher.pdf_resource_requests import PublicResourceRequests
from paper_author_searcher.pdf_search_diagnostics import _resource_statistics, solely_deferred
from paper_author_searcher.pdf_search_status import paper_search_complete, search_satisfaction
from paper_author_searcher.models import PDFFileValidation
from test_http import Response
from test_pdf_downloader import target, fake_client


class RedirectingClient(HttpClient):
    def __init__(self):
        super().__init__(retries=0)
        self.urls = []

    def probe(self, url, maximum_bytes=1024, html_bytes=0, maximum_redirects=5, redirect_guard=None):
        self.urls.append(url)
        destination = 'https://mirror.example.edu/resource'
        if redirect_guard:
            redirect_guard(destination)
        if len(self.urls) == 1:
            raise SourceError('HTTP 429', 429, retry_after=120, endpoint_host='mirror.example.edu')
        return URLProbe(destination, 200, 'text/html', {}, b'complete page', True)


class RoutingTests(unittest.TestCase):
    def requests(self):
        client = RedirectingClient()
        config = replace(DEFAULT_PDF, public_page_request_interval=0)
        return client, PublicResourceRequests(client, config)

    def test_observed_redirect_cooldown_skips_original_request_and_expires(self):
        client, requests = self.requests()
        url = 'https://legacy.example.edu/one'
        with patch('paper_author_searcher.pdf_resource_requests.time.monotonic', return_value=0):
            with self.assertRaises(SourceError):
                requests.probe(url)
            with self.assertRaises(SourceError) as raised:
                requests.probe(url)
            self.assertEqual(raised.exception.request_context['route_kind'], 'observed_redirect')
            self.assertFalse(raised.exception.request_context['request_attempted'])
        self.assertEqual(client.urls, [url])
        self.assertEqual(requests.statistics['request_failures'], 1)
        with patch('paper_author_searcher.pdf_resource_requests.time.monotonic', return_value=121):
            requests.probe(url)
        self.assertEqual(client.urls, [url, url])

    def test_repeated_route_failures_defer_fallback_source_without_hitting_origin_again(self):
        client, requests = self.requests()
        with patch('paper_author_searcher.pdf_resource_requests.time.monotonic', return_value=0):
            for index in range(3):
                with self.assertRaises(SourceError):
                    requests.probe('https://legacy.example.edu/' + str(index), allow_source_backpressure=True)
            with self.assertRaises(SourceError) as raised:
                requests.probe('https://legacy.example.edu/another', allow_source_backpressure=True)
        self.assertEqual(len(client.urls), 3)
        self.assertEqual(raised.exception.request_context['route_kind'], 'source_backpressure')
        self.assertEqual(requests.statistics['source_backpressure_skips'], 1)
        self.assertEqual(requests.statistics['deferred_after_redirect'], 2)
        self.assertEqual(requests.statistics['deferred_before_request'], 1)
        self.assertEqual(len(requests.events), 1)
        self.assertEqual(len(requests.deferrals), 3)

    def test_gateway_with_multiple_observed_destinations_is_not_deferred_as_one_source(self):
        client, requests = self.requests()
        with patch('paper_author_searcher.pdf_resource_requests.time.monotonic', return_value=0):
            for index in range(3):
                with self.assertRaises(SourceError):
                    requests.probe('https://gateway.example.edu/' + str(index), allow_source_backpressure=True)
            requests._remember_redirect('https://gateway.example.edu/healthy', 'https://healthy.example.edu/article')
            with self.assertRaises(SourceError):
                requests.probe('https://gateway.example.edu/another', allow_source_backpressure=True)
        self.assertEqual(len(client.urls), 4)
        self.assertEqual(requests.statistics['source_backpressure_skips'], 0)

    def test_source_backpressure_requires_fallback_opt_in(self):
        client, requests = self.requests()
        with patch('paper_author_searcher.pdf_resource_requests.time.monotonic', return_value=0):
            for index in range(4):
                with self.assertRaises(SourceError):
                    requests.probe('https://legacy.example.edu/' + str(index))
        self.assertEqual(len(client.urls), 4)
        self.assertEqual(requests.statistics['source_backpressure_skips'], 0)

    def test_cached_error_does_not_count_as_new_failure(self):
        client = Mock()
        client.probe.side_effect = SourceError('HTTP 404', 404)
        requests = PublicResourceRequests(client, replace(DEFAULT_PDF, public_page_request_interval=0))
        with self.assertRaises(SourceError):
            requests.probe('https://repo.example.edu/missing')
        with self.assertRaises(SourceError) as raised:
            requests.probe('https://repo.example.edu/missing')
        self.assertEqual(raised.exception.request_context['outcome'], 'cached_failure')
        self.assertFalse(raised.exception.request_context['request_attempted'])
        self.assertEqual(requests.statistics['request_failures'], 1)
        self.assertEqual(requests.statistics['cache_error_hits'], 1)
        client.probe.assert_called_once()


class CompletionTests(unittest.TestCase):
    def full_paper(self, **overrides):
        paper = dict(target(), format_status='valid', identity_status='verified',
                     validation_status='verified', document_type='full_text')
        paper.update(overrides)
        return paper

    def test_satisfied_can_be_true_while_sources_remain_incomplete(self):
        self.assertTrue(search_satisfaction(self.full_paper(), True)[0])
        self.assertFalse(paper_search_complete([{'source': 'public_pages', 'status': 'partial'}]))

    def test_completed_search_can_have_no_confirmed_file(self):
        self.assertTrue(paper_search_complete([{'source': 'public_pages', 'status': 'not_found'}]))
        self.assertFalse(search_satisfaction(self.full_paper(), False)[0])

    def test_unknown_document_type_is_unconfirmed_not_complete(self):
        self.assertIsNone(search_satisfaction(self.full_paper(document_type='unknown'), True)[0])

    def test_poster_slides_supplement_and_unconfirmed_ownership_cannot_satisfy(self):
        for document_type in ('poster', 'slides', 'supplementary_material'):
            self.assertFalse(search_satisfaction(self.full_paper(document_type=document_type), True)[0])
        self.assertFalse(search_satisfaction(self.full_paper(ownership_status='needs_review'), True)[0])

    def test_url_or_format_alone_cannot_satisfy(self):
        self.assertFalse(search_satisfaction(dict(target(), validation_status='verified'), True)[0])
        self.assertFalse(search_satisfaction(self.full_paper(identity_status='needs_review'), True)[0])

    def test_unknown_provider_status_is_not_complete(self):
        self.assertFalse(paper_search_complete([]))
        self.assertFalse(paper_search_complete([{'source': 'public_pages', 'status': 'unknown'}]))

    def test_downloader_saves_satisfaction_separately_from_run_completion(self):
        validator = Mock()
        validator.validate.return_value = PDFFileValidation(
            format_status='valid', identity_status='verified', validation_status='verified', document_type='preprint')
        with tempfile.TemporaryDirectory() as directory:
            manifest = PDFDownloader(fake_client(), validator).download({'papers': [target()]}, Path(directory))
        self.assertTrue(manifest['papers'][0]['search_sufficiently_satisfied'])
        self.assertEqual(manifest['statistics']['search_sufficiently_satisfied_papers'], 1)

    def test_budget_exhaustion_is_not_solely_deferral(self):
        paper = {'providers': [{'source': 'public_pages', 'status': 'partial', 'warnings': [
            {'code': 'source_deferred', 'incomplete': True}, {'code': 'linked_page_limit', 'incomplete': True}]}]}
        self.assertFalse(solely_deferred(paper))
        paper['providers'][0]['warnings'].pop()
        self.assertTrue(solely_deferred(paper))

    def test_legacy_deferred_events_are_excluded_from_request_failure_count(self):
        value = _resource_statistics({'requests': 10, 'cooldown_skips': 5, 'events': [
            {'host': 'mirror.example.edu', 'error_type': 'rate_limited', 'status_code': 429},
            {'host': 'mirror.example.edu', 'error_type': 'source_deferred', 'status_code': 429}]})
        self.assertEqual(value['terminal_request_failures'], 1)
        self.assertEqual(value['terminal_429_failures'], 1)
        self.assertEqual(value['recorded_redirect_deferrals'], 1)
        self.assertIsNone(value['cache_error_hits'])


class BudgetTests(unittest.TestCase):
    def test_cooling_child_does_not_consume_network_budget(self):
        title = target()['title']
        main = 'https://repo.example.edu/index'
        html = ('<a href="https://cold.example.edu/article">' + title + '</a>'
                '<a href="https://good.example.edu/article">' + title + '</a>').encode()
        client = Mock()
        client.probe.side_effect = [URLProbe(main, 200, 'text/html', {}, html),
                                    URLProbe('https://good.example.edu/article', 200, 'application/pdf', {}, b'%PDF-')]
        config = replace(DEFAULT_PDF, public_page_request_interval=0, linked_page_limit=1)
        requests = PublicResourceRequests(client, config)
        requests.hosts['cold.example.edu'] = {'last': None, 'until': 100,
                                             'failures': 1, 'error': SourceError('HTTP 429', 429)}
        with patch('paper_author_searcher.pdf_resource_requests.time.monotonic', return_value=0):
            result = PublicPagePDFProvider(requests, config).find(dict(target(), paper_url=main))
        self.assertEqual(client.probe.call_count, 2)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.status, 'partial')
        self.assertNotIn('linked_page_limit', {item['code'] for item in result.warnings})

    def test_child_network_budget_shared_across_main_pages(self):
        title = target()['title']
        first, second = 'https://repo.example.edu/first', 'https://repo.example.edu/second'
        html = ('<a href="/child">' + title + '</a>').encode()
        other = ('<a href="/other_child">' + title + '</a>').encode()
        client = Mock()
        client.probe.side_effect = [URLProbe(first, 200, 'text/html', {}, html),
                                    URLProbe('https://repo.example.edu/child', 200, 'text/html', {}, b'complete'),
                                    URLProbe(second, 200, 'text/html', {}, other)]
        config = replace(DEFAULT_PDF, public_page_request_interval=0, linked_page_limit=1)
        provider = PublicPagePDFProvider(client, config)
        with patch.object(provider, '_pages', return_value={first: [], second: []}):
            result = provider.find(target())
        self.assertEqual(client.probe.call_count, 3)
        self.assertEqual(result.status, 'partial')
        self.assertTrue(any(warning.get('outcome') == 'query_budget_exhausted' for warning in result.warnings))


class TransportTests(unittest.TestCase):
    def test_each_real_http_retry_error_is_counted_separately(self):
        error = HTTPError('https://api.example.edu', 429, 'limited', {'Retry-After': '0'}, None)
        client = HttpClient(retries=1)
        with patch('paper_author_searcher.http.urlopen', side_effect=[error, Response(b'{}', 'application/json')]), patch(
                'paper_author_searcher.http.time.sleep'):
            client.get('https://api.example.edu/paper')
        stats = client.transport_statistics['metadata_get']
        self.assertEqual(stats['operation_attempts'], 2)
        self.assertEqual(stats['http_error_responses'], {'429': 1})
        self.assertEqual(stats['completed_operations'], 1)

    def test_redirect_guard_error_does_not_count_as_an_http_429_response(self):
        client = HttpClient(retries=1)
        with patch('paper_author_searcher.http.urlopen', side_effect=SourceError(
                'destination cooling', 429, error_type='source_deferred')):
            with self.assertRaises(SourceError):
                client.get('https://gateway.example.edu/paper')
        stats = client.transport_statistics['metadata_get']
        self.assertEqual(stats['http_error_responses'], {})
        self.assertEqual(stats['deferred_operations'], 1)
