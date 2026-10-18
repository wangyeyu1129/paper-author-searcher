"""The metered-API budget refuses requests before they are sent once the run's cap is reached."""

import tempfile
import unittest

from paper_author_searcher.http_budget import (
    BudgetConfig,
    BudgetedHttpClient,
    BudgetExceeded,
    CostLedger,
    classify,
)
from paper_author_searcher.http_cache import CachingHttpClient

OPENALEX = 'https://api.openalex.org'


class Network:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, headers=None, xml=False):
        self.calls.append(url)
        return {'results': []}


class BudgetTests(unittest.TestCase):
    def test_request_kinds_are_priced_by_shape(self):
        self.assertEqual(classify(OPENALEX + '/authors', {'search': 'Mei Lin'}), 'search')
        self.assertEqual(classify(OPENALEX + '/works', {'filter': 'title.search:Deep'}), 'search')
        self.assertEqual(classify(OPENALEX + '/works', {'filter': 'authorships.author.id:A1'}), 'listing')
        self.assertEqual(classify(OPENALEX + '/authors/A1', {}), 'single')

    def test_cap_refuses_before_sending_and_free_hosts_are_unmetered(self):
        network = Network()
        client = BudgetedHttpClient(network, CostLedger(0.0025), BudgetConfig(maximum_cost_usd=0.0025))
        client.get(OPENALEX + '/authors', {'search': 'a'})
        client.get(OPENALEX + '/authors', {'search': 'b'})
        with self.assertRaises(BudgetExceeded):
            client.get(OPENALEX + '/authors', {'search': 'c'})
        client.get(OPENALEX + '/authors/A1')  # single lookups are free
        client.get('https://pub.orcid.org/v3.0/x/works')  # unmetered host
        self.assertEqual(len(network.calls), 4)
        summary = client.ledger.summary()
        self.assertEqual(summary['refused_requests'], 1)
        self.assertAlmostEqual(summary['estimated_cost_usd'], 0.002)

    def test_cached_replays_cost_nothing(self):
        network = Network()
        ledger = CostLedger(0.001)
        with tempfile.TemporaryDirectory() as directory:
            client = CachingHttpClient(
                BudgetedHttpClient(network, ledger, BudgetConfig(maximum_cost_usd=0.001)), directory
            )
            for _ in range(3):
                client.get(OPENALEX + '/authors', {'search': 'same', 'api_key': 'secret'})
        self.assertEqual(len(network.calls), 1)
        self.assertAlmostEqual(ledger.spent_usd, 0.001)

    def test_budget_error_is_a_source_error_so_providers_degrade_gracefully(self):
        from paper_author_searcher.http import SourceError

        self.assertTrue(issubclass(BudgetExceeded, SourceError))


if __name__ == '__main__':
    unittest.main()
