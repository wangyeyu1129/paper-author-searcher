"""Guards for the optimisations recorded in docs/optimization.md.

They pin operation counts and object sharing rather than wall-clock time, so they are deterministic on any machine;
docs/optimization.md has the measured timings.
"""

import unittest
from unittest.mock import patch

from paper_author_searcher import AuthorCandidate as C
from paper_author_searcher import identity, paper_deduplication
from paper_author_searcher.benchmark.runner import window_archive
from paper_author_searcher.benchmark.scoring import scope_to_window
from paper_author_searcher.models import PaperRecord
from paper_author_searcher.paper_deduplication import deduplicate_papers
from paper_author_searcher.resolver import ProfessorResolver

SURNAMES = ['Lin', 'Okafor', 'Ruiz', 'Novak', 'Osei', 'Kim', 'Mora', 'Wei', 'Patel', 'Silva']


class Provider:
    name = 'openalex'

    def __init__(self, records):
        self.records = records

    def search(self, query):
        return list(self.records)


class GroupingCostTests(unittest.TestCase):
    def test_incompatible_names_never_reach_the_full_identity_comparison(self):
        candidates = [
            C('openalex', f'{surname}-{index}', f'Alex {surname}', ['Example University'])
            for surname in SURNAMES
            for index in range(5)
        ]
        resolver = ProfessorResolver([Provider(candidates)])
        with patch('paper_author_searcher.resolver.compare_identity', wraps=identity.compare_identity) as compare:
            groups = resolver._group_candidates(sorted(candidates, key=lambda c: (c.source, c.author_id)))
        # Only same-name profiles are compared in full: at most C(5, 2) per surname, never across surnames.
        self.assertLessEqual(compare.call_count, len(SURNAMES) * 10)
        self.assertEqual(len(groups), len(candidates))


def record(index):
    return PaperRecord(
        source=['openalex', 'semantic_scholar', 'dblp'][index % 3],
        source_paper_id=f'id{index}',
        title='A widely mirrored survey of graph neural networks',
        authors=['Alex Example', 'Taylor Researcher', 'Morgan Writer'],
        year=2020,
        doi='10.1234/survey',
        venue='Journal of Graphs',
        abstract=None,
        paper_url=None,
        source_author_id='A1',
    )


class DeduplicationCostTests(unittest.TestCase):
    def test_each_pair_is_compared_exactly_once(self):
        size = 30
        with patch.object(
            paper_deduplication, 'duplicate_evidence', wraps=paper_deduplication.duplicate_evidence
        ) as evidence:
            papers = deduplicate_papers([record(index) for index in range(size)])
        self.assertEqual(len(papers), 1)
        self.assertEqual(evidence.call_count, size * (size - 1) // 2)


class CopyingTests(unittest.TestCase):
    def collection(self):
        papers = [{'title': f'Paper {year}', 'year': year, 'authors': ['A B']} for year in range(2010, 2024)]
        return {'collection': {'papers': papers, 'all_papers': papers}}

    def test_benchmark_windowing_shares_paper_objects(self):
        archive = self.collection()
        windowed = window_archive(archive, (2015, 2018))
        kept = windowed['collection']['papers']
        self.assertEqual([paper['year'] for paper in kept], [2015, 2016, 2017, 2018])
        self.assertIs(kept[0], archive['collection']['papers'][5])
        self.assertEqual(len(archive['collection']['papers']), 14)  # the caller's archive is untouched

    def test_scoring_scope_shares_paper_objects(self):
        archive = self.collection()
        truth = {'schema_version': 1, 'metadata': {}, 'papers': []}
        scoped = scope_to_window(archive, truth, (2020, 2023))
        self.assertIs(scoped['collection']['papers'][0], archive['collection']['papers'][10])
        self.assertEqual(len(archive['collection']['papers']), 14)


if __name__ == '__main__':
    unittest.main()
