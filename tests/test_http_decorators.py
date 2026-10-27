import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import HttpClient, SourceError, URLProbe
from paper_author_searcher.http_budget import BudgetedHttpClient, CostLedger
from paper_author_searcher.http_cache import CachingHttpClient, cached_client
from paper_author_searcher.http_decorators import HttpClientDecorator, unwrap
from paper_author_searcher.pdf_resource_requests import PublicResourceRequests


class RecordingClient(HttpClient):
    """A network client that records how it was called instead of opening sockets."""

    def __init__(self):
        super().__init__(timeout=7, retries=1)
        self.probes = []

    def probe(self, url, maximum_bytes=1024, html_bytes=0, maximum_redirects=5, redirect_guard=None):
        self.probes.append(redirect_guard)
        if redirect_guard:
            redirect_guard('https://publisher.example.edu/final')
        return URLProbe('https://publisher.example.edu/final', 200, 'application/pdf', {}, b'%PDF-1.7')

    def get(self, url, params=None, headers=None, xml=False):
        return {'url': url}


class DecoratorChainTests(unittest.TestCase):
    def chain(self, directory):
        network = RecordingClient()
        budgeted = BudgetedHttpClient(network, CostLedger(maximum_cost_usd=1.0))
        return network, CachingHttpClient(budgeted, Path(directory))

    def test_every_layer_forwards_operations_and_attributes(self):
        with tempfile.TemporaryDirectory() as directory:
            network, cached = self.chain(directory)
            self.assertEqual(cached.get('https://api.example.org/x'), {'url': 'https://api.example.org/x'})
            self.assertEqual((cached.timeout, cached.retries), (7, 1))
            self.assertIs(cached.transport_statistics, network.transport_statistics)
            self.assertIs(unwrap(cached), network)
            self.assertIsInstance(cached, HttpClientDecorator)

    def test_redirect_guard_reaches_the_network_through_cache_and_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            network, cached = self.chain(directory)
            requests = PublicResourceRequests(cached, replace(DEFAULT_PDF, public_page_request_interval=0))
            requests.probe('https://doi.org/10.1234/x')
        self.assertIsNotNone(network.probes[0])  # previously dropped once the client was wrapped
        self.assertIn('publisher.example.edu', requests.redirect_targets.get('doi.org', set()))

    def test_replay_serves_cached_reads_and_never_reaches_the_network(self):
        with tempfile.TemporaryDirectory() as directory:
            network = RecordingClient()
            cached_client(network, directory, 'record').get('https://api.example.org/x')
            replay = cached_client(network, directory, 'replay')
            self.assertEqual(replay.get('https://api.example.org/x'), {'url': 'https://api.example.org/x'})
            for call in (
                lambda: replay.get('https://api.example.org/missing'),
                lambda: replay.probe('https://homepage.example.edu/'),
                lambda: replay.post_json('https://api.example.org/batch', {}),
            ):
                with self.assertRaises(SourceError):
                    call()
        self.assertEqual(network.probes, [])

    def test_unwrap_returns_plain_clients_unchanged(self):
        client = object()
        self.assertIs(unwrap(client), client)


if __name__ == '__main__':
    unittest.main()
