import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from paper_author_searcher import ProfessorQuery, ProfessorResolver
from paper_author_searcher.cli import main
from paper_author_searcher.config import CollectionConfig
from paper_author_searcher.http import SourceError
from paper_author_searcher.models import AuthorCandidate, PaperRecord, PaperSourceResult
from paper_author_searcher.paper_collector import (
    IdentityConfirmationRequired,
    PaperCollector,
    deduplicate_papers,
)
from paper_author_searcher.paper_providers import (
    DBLPPaperProvider,
    OpenAlexPaperProvider,
    SemanticScholarPaperProvider,
)
from paper_author_searcher.providers import DBLPProvider, OpenAlexProvider, SemanticScholarProvider
from test_resolver import Provider


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, xml=False):
        self.calls.append((url, params, headers, xml))
        if params and params.get('fields') == SemanticScholarPaperProvider.AUTHOR_FIELDS:
            return {'authorId': url.rsplit('/', 1)[-1], 'name': 'Alex Morgan', 'externalIds': {}, 'affiliations': []}
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def paper(source='openalex', paper_id='paper', doi=None, title='A sample study', year=2024):
    return PaperRecord(
        title=title,
        authors=['Alex Morgan', 'Jane Smith'],
        year=year,
        doi=doi,
        venue='Example Journal',
        abstract=None,
        source=source,
        source_paper_id=paper_id,
        source_author_id='author',
        paper_url=None,
    )


def openalex_work(paper_id, author_id='author'):
    return {
        'id': paper_id,
        'title': 'Study ' + paper_id,
        'publication_year': 2024,
        'authorships': [
            {
                'author': {'id': 'https://openalex.org/' + author_id, 'display_name': 'Alex Morgan'},
            }
        ],
        'abstract_inverted_index': {'sample': [1], 'A': [0]},
    }


def profile(status='resolved', aliases=None):
    result = ProfessorResolver(
        [
            Provider(
                [
                    AuthorCandidate('openalex', 'author', 'Alex Morgan', ['Example University']),
                ]
            )
        ]
    ).resolve(ProfessorQuery('Alex Morgan', 'Example University'))
    result.profile.status = status
    if aliases:
        result.profile.author_id_aliases = aliases
    return result


class PaperProviderTests(unittest.TestCase):
    def test_openalex_all_cursor_pages(self):
        client = Client(
            [
                {'results': [openalex_work('first')], 'meta': {'next_cursor': 'second'}},
                {'results': [openalex_work('second')], 'meta': {'next_cursor': None}},
            ]
        )
        adapter = OpenAlexPaperProvider(OpenAlexProvider(client=client))
        result = adapter.collect('author')
        self.assertTrue(result.complete)
        self.assertEqual(len(result.papers), 2)
        self.assertEqual(result.pages_fetched, 2)
        self.assertEqual(client.calls[1][1]['cursor'], 'second')
        self.assertEqual(result.papers[0].abstract, 'A sample')
        self.assertNotIn('search', client.calls[0][1])

    def test_failed_later_page_keeps_earlier_papers(self):
        client = Client(
            [
                {'results': [openalex_work('first')], 'meta': {'next_cursor': 'second'}},
                SourceError('HTTP 429'),
            ]
        )
        result = OpenAlexPaperProvider(OpenAlexProvider(client=client)).collect('author')
        self.assertFalse(result.complete)
        self.assertEqual(len(result.papers), 1)
        self.assertEqual(result.error, 'HTTP 429')

    def test_same_name_wrong_author_id_rejected(self):
        client = Client(
            [
                {'results': [openalex_work('wrong', 'namesake')], 'meta': {'next_cursor': None}},
            ]
        )
        result = OpenAlexPaperProvider(OpenAlexProvider(client=client)).collect('author')
        self.assertTrue(result.pagination_complete)
        self.assertEqual(len(result.papers), 1)
        self.assertIn('Target Author ID absent from authorship metadata', result.papers[0].attribution_evidence)

    def test_repeated_cursor_is_incomplete(self):
        client = Client(
            [
                {'results': [openalex_work('first')], 'meta': {'next_cursor': '*'}},
            ]
        )
        result = OpenAlexPaperProvider(OpenAlexProvider(client=client)).collect('author')
        self.assertFalse(result.complete)
        self.assertIn('repeated', result.error)

    def test_configured_page_limit_marks_incomplete(self):
        client = Client(
            [
                {'results': [openalex_work('first')], 'meta': {'next_cursor': 'next'}},
            ]
        )
        config = CollectionConfig(maximum_pages=1)
        result = OpenAlexPaperProvider(OpenAlexProvider(client=client), config).collect('author')
        self.assertFalse(result.complete)
        self.assertIn('page limit', result.error)

    def test_missing_cursor_metadata_is_not_complete(self):
        client = Client([{'results': []}])
        result = OpenAlexPaperProvider(OpenAlexProvider(client=client)).collect('author')
        self.assertFalse(result.complete)

    def test_semantic_scholar_all_offsets_and_membership(self):
        first = {
            'paperId': 'first',
            'title': 'First study',
            'year': 2024,
            'authors': [{'authorId': 'author', 'name': 'Alex Morgan'}],
            'externalIds': {'DOI': '10.example/ABC'},
        }
        second = dict(first, paperId='second', title='Second study')
        client = Client(
            [
                {'offset': 0, 'next': 1, 'data': [first]},
                {'offset': 1, 'data': [second]},
            ]
        )
        adapter = SemanticScholarPaperProvider(SemanticScholarProvider(client=client))
        result = adapter.collect('author')
        self.assertTrue(result.complete)
        self.assertEqual(len(result.papers), 2)
        self.assertEqual(client.calls[1][1]['offset'], 1)
        self.assertEqual(result.papers[0].doi, '10.example/abc')
        self.assertIn('/author/author/papers', client.calls[0][0])

    def test_semantic_scholar_missing_author_id_rejected(self):
        client = Client(
            [
                {
                    'offset': 0,
                    'data': [
                        {
                            'paperId': 'first',
                            'title': 'First study',
                            'authors': [{'name': 'Alex Morgan'}],
                        }
                    ],
                }
            ]
        )
        result = SemanticScholarPaperProvider(SemanticScholarProvider(client=client)).collect('author')
        self.assertTrue(result.pagination_complete)
        self.assertEqual(len(result.papers), 1)
        self.assertEqual(result.papers[0].ownership_status, 'needs_review')

    def test_dblp_explicit_pid_and_editor_exclusion(self):
        root = ET.fromstring("""
            <dblpperson pid="example/author" n="2">
                <r><article key="journal/first">
                    <author pid="example/author">Alex Morgan</author>
                    <title>A sample study</title><year>2024</year>
                    <journal>Example Journal</journal><ee>https://doi.org/10.example/A</ee>
                </article></r>
                <r><proceedings key="editor/only">
                    <editor pid="example/author">Alex Morgan</editor><title>Edited volume</title>
                </proceedings></r>
            </dblpperson>
        """)
        result = DBLPPaperProvider(DBLPProvider(client=Client([root]))).collect('example/author')
        self.assertEqual(len(result.papers), 2)
        self.assertEqual(result.papers[0].source_author_id, 'example/author')
        self.assertTrue(result.pagination_complete)
        self.assertIn('Target PID absent from author metadata', result.papers[1].attribution_evidence)

    def test_dblp_wrong_pid_is_unavailable(self):
        root = ET.fromstring('<dblpperson pid="another/person"/>')
        result = DBLPPaperProvider(DBLPProvider(client=Client([root]))).collect('example/author')
        self.assertFalse(result.complete)
        self.assertEqual(result.papers, [])

    def test_dblp_html_failure_is_incomplete(self):
        client = Client([SourceError('HTML anti-bot response')])
        result = DBLPPaperProvider(DBLPProvider(client=client)).collect('example/author')
        self.assertFalse(result.complete)
        self.assertIn('anti-bot', result.error)


class PaperDeduplicationTests(unittest.TestCase):
    def test_doi_normalization_preserves_provenance(self):
        records = [
            paper(doi='https://doi.org/10.example/ABC'),
            paper(source='semantic_scholar', paper_id='other', doi='10.example/abc'),
        ]
        papers = deduplicate_papers(records)
        self.assertEqual(len(papers), 1)
        self.assertEqual(len(papers[0].records), 2)

    def test_no_doi_exact_title_year_authors(self):
        records = [paper(), paper(source='semantic_scholar', title='A Sample Study!')]
        self.assertEqual(len(deduplicate_papers(records)), 1)

    def test_versions_and_conflicting_dois_stay_separate(self):
        cases = [
            [paper(doi='10.example/a'), paper(doi='10.example/b')],
            [paper(), paper(paper_id='second', year=2025)],
            [paper(), replace(paper(paper_id='second'), venue='Other Journal')],
            [paper(), replace(paper(paper_id='second'), authors=['Someone Else'])],
            [
                replace(paper(), publication_type='preprint'),
                replace(paper(paper_id='second'), publication_type='article'),
            ],
        ]
        for records in cases:
            with self.subTest(records=records):
                self.assertEqual(len(deduplicate_papers(records)), 2)

    def test_doi_less_bridge_does_not_merge_conflicting_dois(self):
        records = [
            paper(doi='10.example/a'),
            paper(source='dblp'),
            paper(source='semantic_scholar', doi='10.example/b'),
        ]
        self.assertEqual(len(deduplicate_papers(records)), 2)


class FakeProvider:
    name = 'openalex'

    def __init__(self):
        self.ids = []

    def collect(self, author_id):
        self.ids.append(author_id)
        return PaperSourceResult(
            source=self.name,
            author_id=author_id,
            complete=True,
            papers=[replace(paper(), source_author_id=author_id)],
        )


class PaperCollectionTests(unittest.TestCase):
    def test_unconfirmed_identity_blocked_without_requests(self):
        provider = FakeProvider()
        collector = PaperCollector([provider])
        for status in ['ambiguous', 'needs_review']:
            with self.assertRaises(IdentityConfirmationRequired):
                collector.collect(profile(status).profile)
        self.assertEqual(provider.ids, [])

    def test_manual_confirmation_fetches_only_selected_id(self):
        provider = FakeProvider()
        candidate = profile('ambiguous', {'openalex': ['author', 'verified-alias']}).profile
        result = PaperCollector([provider]).collect(candidate, ('openalex', 'author'))
        self.assertEqual(provider.ids, ['author'])
        self.assertEqual(result.identity_confirmation, 'manual_author_id')

    def test_invalid_manual_selection_blocked(self):
        with self.assertRaises(IdentityConfirmationRequired):
            PaperCollector([FakeProvider()]).collect(profile().profile, ('openalex', 'unknown'))

    def test_resolved_verified_aliases_all_retrieved(self):
        provider = FakeProvider()
        candidate = profile(aliases={'openalex': ['author', 'verified-alias']}).profile
        result = PaperCollector([provider]).collect(candidate)
        self.assertEqual(set(provider.ids), {'author', 'verified-alias'})
        self.assertTrue(result.complete)
        self.assertEqual(len(result.papers), 1)
        self.assertEqual(len(result.papers[0].records), 2)

    def test_missing_source_id_and_failure_do_not_erase_other_source(self):
        class Broken:
            name = 'dblp'

            def collect(self, author_id):
                raise SourceError('Network failure')

        candidate = profile().profile
        candidate.author_ids['dblp'] = 'example/author'
        result = PaperCollector([FakeProvider(), Broken()]).collect(candidate)
        self.assertFalse(result.complete)
        self.assertEqual(len(result.papers), 1)
        self.assertIn('Network failure', result.sources[0].error)
        missing = PaperCollector([]).collect(candidate)
        self.assertFalse(missing.complete)

    def test_cli_collects_and_saves_json(self):
        resolver_result = profile()
        collection = PaperCollector([FakeProvider()]).collect(resolver_result.profile)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result.json'
            with patch('paper_author_searcher.composition.ProfessorResolver') as resolver:
                resolver.return_value.resolve.return_value = resolver_result
                with patch('paper_author_searcher.composition.PaperCollector') as collector:
                    collector.return_value.collect.return_value = collection
                    with redirect_stdout(io.StringIO()):
                        code = main(
                            [
                                '--name',
                                'Alex Morgan',
                                '--university',
                                'Example University',
                                '--collect-papers',
                                '--output',
                                str(output),
                            ]
                        )
            self.assertEqual(code, 0)
            data = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(data['collection']['paper_count'], 1)
            self.assertTrue(data['collection']['complete'])

    def test_cli_ambiguous_returns_candidates_without_collecting(self):
        with patch('paper_author_searcher.composition.ProfessorResolver') as resolver:
            resolver.return_value.resolve.return_value = profile('ambiguous')
            with patch('paper_author_searcher.composition.PaperCollector') as collector:
                collector.return_value.collect.side_effect = IdentityConfirmationRequired('Select identity')
                output = io.StringIO()
                with redirect_stdout(output):
                    code = main(['--name', 'Alex Morgan', '--university', 'Example University', '--collect-papers'])
        self.assertEqual(code, 2)
        self.assertIn('candidates', json.loads(output.getvalue()))

    def test_cli_manual_selection_uses_exact_id(self):
        resolver_result = profile('ambiguous')
        collection = PaperCollector([FakeProvider()]).collect(
            resolver_result.profile,
            ('openalex', 'author'),
        )
        with patch('paper_author_searcher.composition.ProfessorResolver') as resolver:
            resolver.return_value.resolve.return_value = resolver_result
            with patch('paper_author_searcher.composition.PaperCollector') as collector:
                collector.return_value.collect.return_value = collection
                with redirect_stdout(io.StringIO()):
                    code = main(
                        [
                            '--name',
                            'Alex Morgan',
                            '--university',
                            'Example University',
                            '--collect-papers',
                            '--select-author',
                            'openalex:author',
                        ]
                    )
                collector.return_value.collect.assert_called_once_with(
                    resolver_result.profile,
                    selected_author=('openalex', 'author'),
                )
        self.assertEqual(code, 0)

    def test_cli_partial_collection_returns_review_exit_code(self):
        resolver_result = profile()
        collection = PaperCollector([FakeProvider()]).collect(resolver_result.profile)
        collection.complete = False
        with patch('paper_author_searcher.composition.ProfessorResolver') as resolver:
            resolver.return_value.resolve.return_value = resolver_result
            with patch('paper_author_searcher.composition.PaperCollector') as collector:
                collector.return_value.collect.return_value = collection
                with redirect_stdout(io.StringIO()):
                    code = main(
                        [
                            '--name',
                            'Alex Morgan',
                            '--university',
                            'Example University',
                            '--collect-papers',
                        ]
                    )
        self.assertEqual(code, 2)


class ConservativeMetadataTests(unittest.TestCase):
    def test_missing_venue_in_one_source_can_match_compatible_metadata(self):
        records = [replace(paper(), venue=None), paper(source='semantic_scholar')]
        self.assertEqual(len(deduplicate_papers(records)), 1)

    def test_empty_bibliography_can_be_complete(self):
        client = Client([{'results': [], 'meta': {'next_cursor': None}}])
        result = OpenAlexPaperProvider(OpenAlexProvider(client=client)).collect('author')
        self.assertTrue(result.complete)
        self.assertEqual(result.papers, [])

    def test_semantic_scholar_later_failure_keeps_first_page(self):
        item = {
            'paperId': 'first',
            'title': 'Study',
            'authors': [{'authorId': 'author', 'name': 'Alex Morgan'}],
        }
        client = Client([{'offset': 0, 'next': 1, 'data': [item]}, SourceError('HTTP 503')])
        result = SemanticScholarPaperProvider(SemanticScholarProvider(client=client)).collect('author')
        self.assertFalse(result.complete)
        self.assertEqual(len(result.papers), 1)

    def test_missing_source_id_is_coverage_gap(self):
        class Missing:
            name = 'dblp'

            def collect(self, author_id):
                raise AssertionError('Must not invent an Author ID')

        result = PaperCollector([FakeProvider(), Missing()]).collect(profile().profile)
        self.assertFalse(result.complete)
        self.assertEqual(len(result.papers), 1)
        self.assertIn('No confirmed Author ID', result.sources[0].error)
