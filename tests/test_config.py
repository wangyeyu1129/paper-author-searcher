"""Configuration injection must change policy without source-code edits."""
import unittest
from dataclasses import replace
from unittest.mock import patch

from paper_author_searcher import AuthorCandidate, ProfessorQuery, ProfessorResolver
from paper_author_searcher.config import MatchingConfig, SourceConfig, HttpConfig
from paper_author_searcher.identity import linked
from paper_author_searcher.http import HttpClient, SourceError
from paper_author_searcher.providers import OpenAlexProvider, SemanticScholarProvider, DBLPProvider
from test_resolver import Provider
from test_providers import Client
from test_http import Response


class ConfigurationTests(unittest.TestCase):
    def test_resolver_uses_injected_threshold(self):
        provider = Provider([
            AuthorCandidate('custom', '1', 'Jane Smith', ['Example University']),
        ])
        query = ProfessorQuery('Jane Smith', 'Example University')
        strict = replace(MatchingConfig(), resolved_confidence=0.95)
        default_result = ProfessorResolver([provider]).resolve(query)
        strict_result = ProfessorResolver([provider], config=strict).resolve(query)
        self.assertEqual(default_result.profile.status, 'resolved')
        self.assertEqual(strict_result.profile.status, 'needs_review')

    def test_identity_policy_is_injectable(self):
        left = AuthorCandidate('first', '1', 'Jane Smith', paper_ids=['doi:a', 'doi:b'])
        right = AuthorCandidate('second', '2', 'Jane Smith', paper_ids=['doi:a', 'doi:b'])
        strict = replace(MatchingConfig(), minimum_shared_dois=3)
        self.assertTrue(linked(left, right))
        self.assertFalse(linked(left, right, strict))

    def test_source_endpoints_and_limits_are_injectable(self):
        sources = SourceConfig(
            openalex_url='https://example.test/openalex/',
            semantic_scholar_url='https://example.test/scholar/',
            dblp_url='https://example.test/dblp/',
            author_limit=4,
            dblp_author_limit=3,
            enrichment_limit=0,
        )
        query = ProfessorQuery('Jane Smith', 'Example University')
        providers = [
            (OpenAlexProvider, {'results': []}, '/openalex/authors', 'per-page', 4),
            (SemanticScholarProvider, {'data': []}, '/scholar/author/search', 'limit', 4),
            (DBLPProvider, {'result': {}}, '/dblp/search/author/api', 'h', 3),
        ]
        for adapter, response, endpoint, limit_key, limit in providers:
            with self.subTest(adapter=adapter.name):
                client = Client([response])
                adapter(client=client, source_config=sources).search(query)
                self.assertEqual(client.calls[0][0], 'https://example.test' + endpoint)
                self.assertEqual(client.calls[0][1][limit_key], limit)

    def test_http_response_limit_is_injectable(self):
        config = replace(HttpConfig(), response_limit=2)
        response = Response(b'{"a":1}', 'application/json')
        with patch('paper_author_searcher.http.urlopen', return_value=response):
            with self.assertRaisesRegex(SourceError, 'size limit'):
                HttpClient(config=config).get('https://example.test')

    def test_default_providers_share_resolver_matching_policy(self):
        config = replace(MatchingConfig(), compatible_name=0.95)
        resolver = ProfessorResolver(config=config)
        self.assertIs(resolver.providers[0].matching_config, config)
