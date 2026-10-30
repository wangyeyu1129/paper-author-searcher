import unittest
from types import SimpleNamespace

from paper_author_searcher import AuthorCandidate as C
from paper_author_searcher import ProfessorQuery, ProfessorResolver
from paper_author_searcher.benchmark.calibration import IdentityCalibrator, markdown, score_selection
from paper_author_searcher.config import MatchingConfig

SCHOOL = ['Example University']


class CountingProvider:
    name = 'openalex'

    def __init__(self, records):
        self.records = records
        self.searches = 0

    def search(self, query):
        self.searches += 1
        return list(self.records)


def person(person_id):
    # Calibration data always names a department; without one a margin never settles same-school namesakes.
    query = ProfessorQuery('Mei Lin', 'Example University', department='Computer Science')
    return SimpleNamespace(person_id=person_id, search_input=SimpleNamespace(to_query=lambda: query))


DIAGNOSIS = {
    'diagnosis': 'found',
    'truth_found_in_any_candidate': True,
    'candidates': [
        {'author_ids': {'openalex': ['A1']}, 'truth_hits': 3, 'window_works': 5},
        {'author_ids': {'openalex': ['B1']}, 'truth_hits': 0, 'window_works': 4},
    ],
}


class ScoringTests(unittest.TestCase):
    def test_coverage_counts_truth_hits_reached_and_purity_counts_bearing_ids(self):
        self.assertEqual(score_selection({('openalex', 'A1')}, DIAGNOSIS), (1.0, 1.0))
        self.assertEqual(score_selection({('openalex', 'B1')}, DIAGNOSIS), (0.0, 0.0))
        self.assertEqual(score_selection({('openalex', 'A1'), ('openalex', 'B1')}, DIAGNOSIS), (1.0, 0.5))
        self.assertEqual(score_selection(set(), DIAGNOSIS), (0.0, None))


class SearchTests(unittest.TestCase):
    def setUp(self):
        # Two namesakes at the target school: A1 scores slightly higher, so a wide margin makes the contest ambiguous.
        self.provider = CountingProvider(
            [C('openalex', 'A1', 'Mei Lin', SCHOOL, topics=['Robustness']), C('openalex', 'B1', 'Mei Lin', SCHOOL)]
        )

        def factory(config):
            return ProfessorResolver([self.provider], config=config)

        self.calibrator = IdentityCalibrator(factory)
        self.people = [person('p1')]
        self.diagnoses = {'p1': DIAGNOSIS}

    def test_search_prefers_coverage_then_the_smallest_change_from_defaults(self):
        base = MatchingConfig()
        report = self.calibrator.search(
            base, {'ambiguity_margin': [0.0, 0.5]}, self.people, self.people, self.diagnoses, 0.95
        )
        recommended = report['recommended']
        self.assertEqual(recommended['parameters'], {'ambiguity_margin': 0.0})
        self.assertEqual(recommended['tune']['resolved'], 1)
        self.assertEqual(recommended['tune']['resolved_coverage'], 1.0)
        wide = next(trial for trial in report['trials'] if trial['parameters'] == {'ambiguity_margin': 0.5})
        self.assertEqual(wide['tune']['resolved'], 0)
        self.assertTrue(report['purity_floor_met'])
        self.assertIn('Recommended parameters', markdown(report))

    def test_provider_searches_are_memoised_across_trials(self):
        self.calibrator.search(
            MatchingConfig(), {'ambiguity_margin': [0.0, 0.01, 0.5]}, self.people, self.people, self.diagnoses, 0.95
        )
        self.assertEqual(self.provider.searches, 1)

    def test_people_without_truth_or_with_inconclusive_diagnoses_are_skipped(self):
        diagnoses = {'p1': {'diagnosis': 'no_truth', 'candidates': []}}
        self.assertEqual(self.calibrator.evaluate(MatchingConfig(), self.people, diagnoses), [])


if __name__ == '__main__':
    unittest.main()
