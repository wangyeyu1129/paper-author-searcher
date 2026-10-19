import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from paper_author_searcher import AuthorCandidate, ProfessorQuery, ProfessorResolver
from paper_author_searcher.cli import main
from paper_author_searcher.identity import linked
from test_resolver import Provider


class IdentityTests(unittest.TestCase):
    def resolve_records(self, records, name='Alex Morgan', university='Example University'):
        resolver = ProfessorResolver([Provider(records)])
        return resolver.resolve(ProfessorQuery(name, university))

    def test_missing_school_is_neutral(self):
        missing = self.resolve_records(
            [
                AuthorCandidate('semantic_scholar', 'missing', 'Alex Morgan'),
            ]
        ).profile
        mismatch = self.resolve_records(
            [
                AuthorCandidate('semantic_scholar', 'mismatch', 'Alex Morgan', ['Other University']),
            ]
        ).profile
        self.assertEqual(missing.university_status, 'unknown')
        self.assertEqual(missing.status, 'needs_review')
        self.assertGreater(missing.identity_confidence, mismatch.identity_confidence)
        self.assertEqual(mismatch.university_status, 'mismatch')

    def test_namesakes_across_languages_and_fields(self):
        cases = [
            ('Alex Morgan', 'Example University', 'Ecology'),
            ('Élodie Martin', 'Université Exemple', 'History'),
            ('李明', '示例大学', 'Materials Science'),
        ]
        for name, university, topic in cases:
            with self.subTest(name=name, topic=topic):
                records = [
                    AuthorCandidate(
                        'openalex',
                        'target',
                        name,
                        [university],
                        topics=[topic],
                        paper_ids=['doi:10.example/a', 'doi:10.example/b'],
                    ),
                    AuthorCandidate(
                        'semantic_scholar',
                        'verified',
                        name,
                        paper_ids=['https://doi.org/10.example/A', 'doi:10.example/b'],
                    ),
                    AuthorCandidate('dblp', 'namesake', name, [university]),
                ]
                result = self.resolve_records(records, name, university)
                self.assertEqual(result.profile.author_ids.get('semantic_scholar'), 'verified')
                self.assertNotIn('dblp', result.profile.author_ids)
                self.assertEqual(len(result.candidates), 2)

    def test_school_alone_never_merges(self):
        left = AuthorCandidate('openalex', 'left', 'Alex Morgan', ['Example University'])
        right = AuthorCandidate('dblp', 'right', 'Alex Morgan', ['Example University'])
        self.assertFalse(linked(left, right))

    def test_single_doi_insufficient(self):
        left = AuthorCandidate('openalex', 'left', 'Alex Morgan', paper_ids=['doi:example/a'])
        right = AuthorCandidate('dblp', 'right', 'Alex Morgan', paper_ids=['doi:example/a'])
        self.assertFalse(linked(left, right))

    def test_coauthor_and_topic_support(self):
        evidence = {
            'affiliations': ['Example University'],
            'coauthors': ['Jane Smith', 'John Doe', 'Sam Jones'],
            'topics': ['Ecology', 'Biodiversity'],
        }
        left = AuthorCandidate('openalex', 'left', 'Alex Morgan', **evidence)
        right = AuthorCandidate('dblp', 'right', 'Alex Morgan', **evidence)
        self.assertTrue(linked(left, right))

    def test_orcid_conflict_overrides_dois(self):
        left = AuthorCandidate(
            'openalex',
            'left',
            'Alex Morgan',
            orcid='orcid-left',
            paper_ids=['doi:example/a', 'doi:example/b'],
        )
        right = AuthorCandidate(
            'dblp',
            'right',
            'Alex Morgan',
            orcid='orcid-right',
            paper_ids=['doi:example/a', 'doi:example/b'],
        )
        self.assertFalse(linked(left, right))

    def test_same_source_distinct_ids_stay_separate(self):
        left = AuthorCandidate('openalex', 'left', 'Alex Morgan', orcid='same-orcid')
        right = AuthorCandidate('openalex', 'right', 'Alex Morgan', orcid='different-orcid')
        self.assertFalse(linked(left, right))

    def test_missing_middle_name_is_compatible(self):
        left = AuthorCandidate('openalex', 'left', 'Alex E. Morgan', orcid='same-orcid')
        right = AuthorCandidate('dblp', 'right', 'Alex Morgan', orcid='same-orcid')
        self.assertTrue(linked(left, right))

    def test_conflicting_middle_names_block_merge(self):
        left = AuthorCandidate('openalex', 'left', 'Alex E. Morgan', orcid='same-orcid')
        right = AuthorCandidate('dblp', 'right', 'Alex T. Morgan', orcid='same-orcid')
        self.assertFalse(linked(left, right))

    def test_cli_verbose(self):
        result = self.resolve_records(
            [
                AuthorCandidate('openalex', 'target', 'Alex Morgan', ['Example University']),
            ]
        )
        for verbose in (False, True):
            arguments = ['--name', 'Alex Morgan', '--university', 'Example University']
            if verbose:
                arguments.append('--verbose')
            output = io.StringIO()
            with patch('paper_author_searcher.composition.ProfessorResolver') as factory:
                factory.return_value.resolve.return_value = result
                with redirect_stdout(output):
                    self.assertEqual(main(arguments), 0)
            data = json.loads(output.getvalue())
            self.assertEqual('candidates' in data, verbose)
            if not verbose:
                self.assertEqual(data['university'], 'Example University')
                self.assertNotIn('research_interests', data)

    def test_single_paper_fragment_does_not_outrank_current_anchor(self):
        records = [
            AuthorCandidate(
                'openalex',
                'anchor',
                'Alex Morgan',
                ['Example University'],
                ['Example University'],
            ),
            AuthorCandidate(
                'openalex',
                'fragment',
                'Alex Y. Morgan',
                ['Example University'],
                ['Example University'],
                aliases=['Alex Morgan'],
                paper_ids=['doi:example/a'],
                coauthors=['Jane Smith', 'John Doe'],
            ),
            AuthorCandidate(
                'semantic_scholar',
                'fragment',
                'Alex Y. Morgan',
                paper_ids=['doi:example/a'],
                coauthors=['Jane Smith', 'John Doe'],
            ),
        ]
        result = self.resolve_records(records)
        self.assertEqual(result.profile.author_ids['openalex'], 'anchor')
        self.assertEqual(result.profile.status, 'ambiguous')

    def test_bridge_record_cannot_merge_conflicting_identities(self):
        records = [
            AuthorCandidate('first', 'a', 'Alex Morgan', orcid='orcid-left'),
            AuthorCandidate(
                'second',
                'b',
                'Alex Morgan',
                orcid='orcid-left',
                paper_ids=['doi:example/a', 'doi:example/b'],
            ),
            AuthorCandidate(
                'third',
                'c',
                'Alex Morgan',
                orcid='orcid-right',
                paper_ids=['doi:example/a', 'doi:example/b'],
            ),
        ]
        result = self.resolve_records(records)
        self.assertEqual(len(result.candidates), 2)
        for profile in result.candidates:
            self.assertFalse('first' in profile.author_ids and 'third' in profile.author_ids)


class NameBoundTests(unittest.TestCase):
    """The grouping shortcut is exact only if the bound never falls below the real similarity."""

    NAMES = (
        'Wei Wang',
        'Wang Wei',
        'W. Wang',
        'Wei Wang 2019',
        'Wei-Wang',
        'Weiwei Wang',
        'Wei Huang',
        'Mei Lin',
        'Mei Lim',
        'M Lin',
        'Christopher D. Manning',
        'Christopher Manning',
        'Christopher Mannig',
        'C. D. Manning',
        'Chris Manning',
        'Zoë Smith-Jones',
        'Zoe Smith Jones',
        'Huan Zhang',
        'Zhang Huan',
        'H. Zhang',
        'Hao Zhang',
        'Aarti Singh',
        'A. Singh',
        'Aarti S. Singh',
        'Aarti R. Singh',
        'Yi Cui',
        'Yi  Cui',
        'Cui Yi',
        'J. P. Smith',
        'John Paul Smith',
        'John Smith',
        'Jon Smith',
        'José Álvarez',
        'Jose Alvarez',
        '',
        'X',
    )

    def test_upper_bound_dominates_similarity_for_every_pair(self):
        from paper_author_searcher.matching import name_similarity, name_similarity_upper_bound

        for left in self.NAMES:
            for right in self.NAMES:
                with self.subTest(left=left, right=right):
                    self.assertGreaterEqual(name_similarity_upper_bound(left, right), name_similarity(left, right))


class SimilarityCacheTests(unittest.TestCase):
    def test_statistics_and_clearing(self):
        from paper_author_searcher.matching import clear_similarity_caches, name_similarity, similarity_cache_statistics

        clear_similarity_caches()
        name_similarity('Mei Lin', 'Mei Lin')
        name_similarity('Mei Lin', 'Mei Lin')
        statistics = similarity_cache_statistics()['name_similarity']
        self.assertEqual((statistics['hits'], statistics['misses'], statistics['entries']), (1, 1, 1))
        self.assertEqual(statistics['hit_rate'], 0.5)
        clear_similarity_caches()
        self.assertEqual(similarity_cache_statistics()['name_similarity']['entries'], 0)
