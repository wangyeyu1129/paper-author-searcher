import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import Mock

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import URLProbe
from paper_author_searcher.models import PDFProviderResult
from paper_author_searcher.pacing import RequestPacer
from paper_author_searcher.pdf_resolver import PDFResolver
from paper_author_searcher.pdf_resource_requests import PublicResourceRequests


class PacerTests(unittest.TestCase):
    def test_same_key_callers_are_spaced_and_other_keys_are_not(self):
        clock = [100.0]
        sleeps = []
        pacer = RequestPacer(2, clock=lambda: clock[0], sleep=sleeps.append)
        self.assertEqual([pacer.wait('a'), pacer.wait('a'), pacer.wait('a')], [0, 2, 4])
        self.assertEqual(pacer.wait('b'), 0)
        self.assertEqual(sleeps, [2, 4])

    def test_concurrent_callers_get_distinct_slots(self):
        pacer = RequestPacer(0.05)
        starts = []
        lock = threading.Lock()

        def call():
            pacer.wait('host')
            with lock:
                starts.append(time.monotonic())

        threads = [threading.Thread(target=call) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        gaps = [later - earlier for earlier, later in zip(sorted(starts), sorted(starts)[1:])]
        self.assertTrue(all(gap >= 0.04 for gap in gaps), gaps)


class SlowProvider:
    name = 'slow'

    def __init__(self, delay):
        self.delay = delay
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()

    def find(self, paper):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        return PDFProviderResult(self.name, status='not_found')


def collection(count):
    papers = [{'title': f'Paper {index}', 'ownership_status': 'verified'} for index in range(count)]
    papers.append({'title': 'Not mine', 'ownership_status': 'rejected'})
    return {'papers': papers}


class ResolverConcurrencyTests(unittest.TestCase):
    def resolve(self, workers, count=8, **options):
        provider = SlowProvider(0.05)
        resolver = PDFResolver([provider], Mock(), config=replace(DEFAULT_PDF, lookup_workers=workers))
        progress = []
        result = resolver.resolve(collection(count), progress=lambda *args: progress.append(args), **options)
        return result, provider, progress

    def test_papers_are_resolved_concurrently_in_input_order(self):
        result, provider, progress = self.resolve(workers=4)
        self.assertGreater(provider.peak, 1)
        self.assertEqual([paper.title for paper in result.papers], [f'Paper {i}' for i in range(8)] + ['Not mine'])
        self.assertEqual(result.papers[-1].validation_status, 'skipped_ownership')
        self.assertEqual([entry[1] for entry in progress], list(range(1, 9)))

    def test_one_worker_stays_sequential(self):
        _, provider, _ = self.resolve(workers=1)
        self.assertEqual(provider.peak, 1)

    def test_paper_limit_applies_before_concurrency(self):
        result, _, _ = self.resolve(workers=4, maximum_papers=3)
        statuses = [paper.validation_status for paper in result.papers[:8]]
        self.assertEqual(statuses[3:], ['not_processed'] * 5)

    def test_invalid_worker_count_is_rejected(self):
        with self.assertRaises(ValueError):
            replace(DEFAULT_PDF, lookup_workers=0)


class SchedulerConcurrencyTests(unittest.TestCase):
    def test_parallel_probes_keep_exact_counters_and_cache(self):
        client = Mock()
        client.probe.side_effect = lambda url, *args: URLProbe(url, 200, 'application/pdf', {}, b'%PDF-1.7')
        requests = PublicResourceRequests(client, replace(DEFAULT_PDF, public_page_request_interval=0))
        urls = [f'https://host{index % 3}.example/paper{index}.pdf' for index in range(30)]
        threads = [threading.Thread(target=requests.probe, args=(url,)) for url in urls * 2]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        statistics = requests.statistics
        self.assertEqual(statistics['requests'] + statistics['cache_hits'], 60)
        self.assertEqual(len(requests.cache), 30)
        self.assertEqual(requests.cache_bytes, 30 * len(b'%PDF-1.7'))


if __name__ == '__main__':
    unittest.main()
