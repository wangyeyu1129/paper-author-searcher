import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import HttpClient, SourceError, URLProbe
from paper_author_searcher.http_budget import BudgetedHttpClient, CostLedger
from paper_author_searcher.http_cache import CachingHttpClient, cached_client
from paper_author_searcher.http_decorators import HttpClientDecorator, PacedHttpClient, unwrap
from paper_author_searcher.pacing import RequestPacer
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

    def test_configured_hosts_keep_their_interval_and_others_do_not_wait(self):
        clock, sleeps = [0.0], []
        paced = PacedHttpClient(
            RecordingClient(),
            [('export.arxiv.org', 3.0)],
            lambda interval: RequestPacer(interval, clock=lambda: clock[0], sleep=sleeps.append),
        )
        for _ in range(3):
            paced.get('https://export.arxiv.org/api/query')
        paced.get('https://api.openalex.org/works')
        self.assertEqual(sleeps, [3.0, 6.0])

    def test_unwrap_returns_plain_clients_unchanged(self):
        client = object()
        self.assertIs(unwrap(client), client)


if __name__ == '__main__':
    unittest.main()


class ScriptedClient(HttpClient):
    """Answers each probe from a script of responses or errors, redirecting through one hop first."""

    def __init__(self, *outcomes):
        super().__init__(retries=0)
        self.outcomes = list(outcomes)
        self.calls = 0

    def probe(self, url, maximum_bytes=1024, html_bytes=0, maximum_redirects=5, redirect_guard=None):
        self.calls += 1
        if redirect_guard:
            redirect_guard('https://mirror.example.edu/paper.pdf')
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def pdf_probe(prefix=b'%PDF-1.7 body', **headers):
    headers = {'Content-Range': 'bytes 0-12/13', 'Set-Cookie': 'session=secret', **headers}
    return URLProbe('https://mirror.example.edu/paper.pdf', 206, 'application/pdf', headers, prefix, True)


class ProbeCacheTests(unittest.TestCase):
    url = 'https://doi.org/10.1234/x'

    def test_a_recorded_probe_replays_offline_with_its_redirect_hops(self):
        with tempfile.TemporaryDirectory() as directory:
            network = ScriptedClient(pdf_probe())
            recorded = cached_client(network, directory, 'record').probe(self.url)
            hops = []
            replayed = cached_client(ScriptedClient(), directory, 'replay').probe(self.url, redirect_guard=hops.append)
            stored = list(Path(directory).rglob('*.probe.json'))
            self.assertNotIn('secret', stored[0].read_text())
        self.assertEqual(network.calls, 1)
        self.assertEqual(hops, ['https://mirror.example.edu/paper.pdf'])
        self.assertEqual((replayed.url, replayed.status, replayed.prefix), (recorded.url, 206, b'%PDF-1.7 body'))
        self.assertEqual(replayed.headers, {'Content-Range': 'bytes 0-12/13'})
        self.assertTrue(replayed.body_complete)

    def test_a_smaller_read_is_cut_from_the_recorded_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            cached_client(ScriptedClient(pdf_probe()), directory, 'record').probe(self.url)
            replayed = cached_client(ScriptedClient(), directory, 'replay').probe(self.url, maximum_bytes=4)
        self.assertEqual((replayed.prefix, replayed.body_complete), (b'%PDF', False))

    def test_a_larger_read_than_recorded_is_a_miss(self):
        page = URLProbe('https://mirror.example.edu/landing', 200, 'text/html', {}, b'<html>' * 10, False)
        with tempfile.TemporaryDirectory() as directory:
            cached_client(ScriptedClient(page), directory, 'record').probe(self.url, html_bytes=60)
            with self.assertRaises(SourceError) as raised:
                cached_client(ScriptedClient(), directory, 'replay').probe(self.url, html_bytes=1000)
        self.assertEqual(raised.exception.error_type, 'cache_miss')

    def test_a_recorded_failure_replays_but_recording_probes_it_again(self):
        refused = SourceError('HTTP 503', 503, endpoint_host='mirror.example.edu')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SourceError):
                cached_client(ScriptedClient(refused), directory, 'record').probe(self.url)
            with self.assertRaises(SourceError) as raised:
                cached_client(ScriptedClient(), directory, 'replay').probe(self.url)
            again = ScriptedClient(pdf_probe())
            self.assertEqual(cached_client(again, directory, 'record').probe(self.url).status, 206)
        self.assertEqual((raised.exception.status_code, raised.exception.error_type), (503, 'server_error'))
        self.assertEqual(raised.exception.endpoint_host, 'mirror.example.edu')
        self.assertEqual(again.calls, 1)

    def test_scheduler_deferrals_are_not_recorded(self):
        deferred = SourceError('Host temporarily deferred', error_type='source_deferred')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SourceError):
                cached_client(ScriptedClient(deferred), directory, 'record').probe(self.url)
            self.assertEqual(list(Path(directory).rglob('*.probe.json')), [])

    def test_the_scheduler_does_not_pace_an_answer_from_the_cache(self):
        clock, sleeps = [0.0], []
        with tempfile.TemporaryDirectory() as directory:
            cached_client(ScriptedClient(pdf_probe()), directory, 'record').probe(self.url)
            requests = PublicResourceRequests(cached_client(ScriptedClient(), directory, 'replay'))
            requests.pacer = RequestPacer(5.0, clock=lambda: clock[0], sleep=sleeps.append)
            for _ in range(2):
                requests.cache.clear()
                requests.probe(self.url)
        self.assertEqual(sleeps, [])
        self.assertIn('mirror.example.edu', requests.redirect_targets['doi.org'])


class PostingClient(HttpClient):
    def __init__(self):
        super().__init__(retries=0)
        self.posts = []

    def post_json(self, url, payload, params=None, headers=None, expected_type=dict):
        self.posts.append((url, payload, params))
        return [{'paperId': value} for value in payload['ids']]


class PostCacheTests(unittest.TestCase):
    def test_a_recorded_post_replays_offline_and_its_secret_never_reaches_the_key(self):
        url = 'https://api.semanticscholar.org/graph/v1/paper/batch'
        with tempfile.TemporaryDirectory() as directory:
            network = PostingClient()
            recorded = cached_client(network, directory, 'record')
            recorded.post_json(url, {'ids': ['a', 'b']}, params={'api_key': 'secret'})
            recorded.post_json(url, {'ids': ['a', 'b']}, params={'api_key': 'other'})
            replay = cached_client(PostingClient(), directory, 'replay')
            self.assertEqual(replay.post_json(url, {'ids': ['a', 'b']}), [{'paperId': 'a'}, {'paperId': 'b'}])
            with self.assertRaises(SourceError):
                replay.post_json(url, {'ids': ['c']})
            self.assertNotIn('secret', ''.join(path.read_text() for path in Path(directory).rglob('*.json')))
        self.assertEqual(len(network.posts), 1)
