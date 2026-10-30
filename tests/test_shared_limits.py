"""Limits shared across workers: host pacing, Retry-After holds and the daily budget."""

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from paper_author_searcher.composition import RunOptions, ServiceContainer, TransportLimits
from paper_author_searcher.http import SourceError
from paper_author_searcher.http_budget import BudgetedHttpClient, BudgetExceeded, CostLedger
from paper_author_searcher.http_decorators import PacedHttpClient
from paper_author_searcher.pacing import RequestPacer
from paper_author_searcher.service.shared_limits import SqliteSharedLimits
from paper_author_searcher.settings import Settings


class Clock:
    def __init__(self):
        self.now = 1_000_000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))


class Refusing:
    def __init__(self, retry_after):
        self.retry_after = retry_after
        self.calls = 0

    def get(self, url, params=None, headers=None, xml=False):
        self.calls += 1
        raise SourceError('slow down', status_code=429, retry_after=self.retry_after)

    def post_json(self, url, payload, params=None, headers=None, expected_type=dict):
        self.calls += 1
        return {'ok': True}


def reserve_many(path, count, queue):
    limits = SqliteSharedLimits(path, 1.0)
    queue.put([limits.reserve_slot('host', 0.01) for _ in range(count)])


class SharedPacingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'limits.sqlite'
        self.clock = Clock()
        self.limits = SqliteSharedLimits(self.path, 1.0, clock=self.clock, sleep=self.clock.sleep)

    def test_two_pacers_over_one_database_take_turns(self):
        other = SqliteSharedLimits(self.path, 1.0, clock=self.clock, sleep=self.clock.sleep)
        first, second = self.limits.pacer(2.0), other.pacer(2.0)
        self.assertEqual(first.wait('h'), 0.0)
        self.assertEqual(second.wait('h'), 2.0)
        self.assertEqual(first.wait('h'), 4.0)
        self.assertEqual(self.clock.sleeps, [2.0, 4.0])
        self.assertEqual(first.wait('another-host'), 0.0)

    def test_processes_never_share_a_slot(self):
        context = multiprocessing.get_context('spawn')
        queue = context.Queue()
        workers = [context.Process(target=reserve_many, args=(self.path, 25, queue)) for _ in range(3)]
        for worker in workers:
            worker.start()
        delays = [delay for _ in workers for delay in queue.get(timeout=60)]
        for worker in workers:
            worker.join()
        self.assertEqual(len(delays), 75)
        # 75 slots 10 ms apart: the last caller is told to wait far longer than one process's 25 slots would need.
        self.assertGreater(max(delays), 0.25)

    def test_a_hold_delays_every_worker(self):
        self.limits.hold('h', 30.0)
        other = SqliteSharedLimits(self.path, 1.0, clock=self.clock, sleep=self.clock.sleep)
        self.assertEqual(other.pacer(1.0).wait('h'), 30.0)

    def test_an_unreachable_database_never_blocks_a_request(self):
        self.path.unlink()
        self.path.mkdir()
        self.assertEqual(self.limits.reserve_slot('h', 1.0), 0.0)
        self.limits.hold('h', 1.0)
        self.limits.charge('h', 5.0)


class DailyBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.clock = Clock()
        self.limits = SqliteSharedLimits(Path(self.directory.name) / 'limits.sqlite', 1.0, clock=self.clock)

    def test_spend_is_capped_per_host_and_resets_each_day(self):
        self.limits.charge('a', 0.6)
        with self.assertRaises(BudgetExceeded):
            self.limits.charge('a', 0.6)
        self.assertAlmostEqual(self.limits.spent_today('a'), 0.6)
        self.limits.charge('b', 0.6)
        self.clock.now += 86_400
        self.limits.charge('a', 0.6)
        self.assertAlmostEqual(self.limits.spent_today('a'), 0.6)

    def test_metered_requests_count_against_the_day_and_free_ones_do_not(self):
        class Network:
            def get(self, url, params=None, headers=None, xml=False):
                return {}

        client = BudgetedHttpClient(Network(), CostLedger(10.0), daily=self.limits)
        client.get('https://api.openalex.org/works', {'search': 'x'})
        client.get('https://api.openalex.org/works/W1')
        self.assertAlmostEqual(self.limits.spent_today('api.openalex.org'), 0.001)


class PacedClientTests(unittest.TestCase):
    def test_a_refusal_holds_the_host_for_its_retry_after_up_to_the_cap(self):
        clock = Clock()
        pacer = RequestPacer(1.0, clock=clock, sleep=clock.sleep)
        network = Refusing(retry_after=3600)
        client = PacedHttpClient(network, [('h.example', 1.0)], lambda _: pacer, maximum_hold=60.0)
        with self.assertRaises(SourceError):
            client.get('https://h.example/x')
        self.assertEqual(pacer.wait('h.example'), 60.0)

    def test_posts_are_paced_too(self):
        clock = Clock()
        pacer = RequestPacer(5.0, clock=clock, sleep=clock.sleep)
        client = PacedHttpClient(Refusing(None), [('h.example', 5.0)], lambda _: pacer)
        client.post_json('https://h.example/x', {})
        client.post_json('https://h.example/x', {})
        self.assertEqual(clock.sleeps, [5.0])

    def test_a_container_uses_the_limits_it_is_given_and_passes_them_on(self):
        used = []

        def factory(interval):
            used.append(interval)
            return RequestPacer(interval)

        container = ServiceContainer(Settings(), RunOptions(), transport_limits=TransportLimits(pacer_factory=factory))
        self.assertIs(container.with_settings(Settings()).transport_limits, container.transport_limits)
        self.assertIsNotNone(container.http_client)
        self.assertEqual(used, [3.0])


if __name__ == '__main__':
    unittest.main()
