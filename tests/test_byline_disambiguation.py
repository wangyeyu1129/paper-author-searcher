import unittest
from dataclasses import replace

from paper_author_searcher import AuthorCandidate, ProfessorQuery, ProfessorResolver
from paper_author_searcher.byline_disambiguation import BylineDisambiguator
from paper_author_searcher.config import DEFAULT_DISAMBIGUATION
from paper_author_searcher.identity_anchors import IdentityAnchors
from paper_author_searcher.status import AnchorKind
from test_resolver import Provider

QUERY = ProfessorQuery('Alex Morgan', 'Example University', department='Materials Science and Engineering')
HOME = 'Department of Materials Science and Engineering, Example University, Springfield'
MEDICINE = 'Department of Medicine, Example University'
SCHOOL = 'School of Engineering and Applied Sciences, Example University'


def namesake(author_id, bylines):
    return AuthorCandidate(
        'openalex', author_id, 'Alex Morgan', ['Example University'], bylines=[(line,) for line in bylines]
    )


class BylineDisambiguationTests(unittest.TestCase):
    def resolve(self, records, query=QUERY, config=DEFAULT_DISAMBIGUATION, anchors=None):
        resolver = ProfessorResolver([Provider(records)], disambiguator=BylineDisambiguator(config))
        if anchors is not None:
            resolver.anchor_finder = anchors
        return resolver.resolve(query)

    def test_department_bylines_settle_a_tie_between_namesakes(self):
        result = self.resolve([namesake('A1', [MEDICINE] * 9), namesake('A2', [HOME] * 6)])
        self.assertEqual(result.profile.author_ids, {'openalex': 'A2'})
        self.assertEqual(result.profile.status, 'resolved')
        self.assertIsNone(result.ambiguity)
        self.assertIn('6 sampled works print Materials Science and Engineering', result.profile.disambiguation)

    def test_department_evidence_must_dominate(self):
        # 6 against 3 is below the 3x dominance ratio: the contest stays open.
        result = self.resolve([namesake('A1', [HOME] * 3), namesake('A2', [HOME] * 6)])
        self.assertEqual(result.profile.status, 'ambiguous')
        self.assertIsNone(result.profile.disambiguation)

    def test_thin_department_evidence_leaves_the_contest_open(self):
        result = self.resolve([namesake('A1', [MEDICINE] * 20), namesake('A2', [HOME] * 4)])
        self.assertEqual(result.profile.status, 'ambiguous')

    def test_any_department_evidence_blocks_the_school_tier(self):
        # A long-tenured namesake elsewhere at the school must not beat two thinly evidenced department matches.
        result = self.resolve(
            [namesake('A1', [MEDICINE] * 30), namesake('A2', [HOME]), namesake('A3', [HOME])],
            config=replace(DEFAULT_DISAMBIGUATION, school_fallback=True),
        )
        self.assertEqual(result.profile.status, 'ambiguous')

    def test_school_bylines_decide_when_enabled_and_no_byline_names_the_department(self):
        records = [namesake('A1', [SCHOOL] * 8), namesake('A2', ['Tsinghua University', SCHOOL])]
        result = self.resolve(records, config=replace(DEFAULT_DISAMBIGUATION, school_fallback=True))
        self.assertEqual(result.profile.author_ids, {'openalex': 'A1'})
        self.assertIn('print Example University', result.profile.disambiguation)

    def test_school_tier_is_off_by_default(self):
        records = [namesake('A1', [SCHOOL] * 8), namesake('A2', [SCHOOL])]
        self.assertEqual(self.resolve(records).profile.status, 'ambiguous')

    def test_disabled_disambiguation_changes_nothing(self):
        records = [namesake('A1', [MEDICINE] * 9), namesake('A2', [HOME] * 6)]
        config = replace(DEFAULT_DISAMBIGUATION, enabled=False)
        self.assertEqual(self.resolve(records, config=config).profile.status, 'ambiguous')

    def test_works_at_another_school_are_not_evidence(self):
        elsewhere = 'Department of Materials Science and Engineering, Other University'
        result = self.resolve([namesake('A1', [elsewhere] * 9), namesake('A2', [MEDICINE])])
        self.assertEqual(result.profile.status, 'ambiguous')

    def test_an_anchored_contest_is_left_to_the_anchor(self):
        class Anchors:
            def find(self, query):
                found = IdentityAnchors()
                found.add('openalex', 'A1', 'listed paper', AnchorKind.PAPER)
                return found

        result = self.resolve([namesake('A1', [MEDICINE]), namesake('A2', [HOME] * 9)], anchors=Anchors())
        self.assertEqual(result.profile.author_ids, {'openalex': 'A1'})
        self.assertIsNone(result.profile.disambiguation)

    def test_evidence_counts_works_not_lines(self):
        candidate = namesake('A1', [])
        candidate.bylines = [(HOME, 'Example University'), (MEDICINE,), ('Other University',)]
        evidence = BylineDisambiguator().evidence(QUERY, [candidate])
        self.assertEqual((evidence.department, evidence.school), (1, 2))


if __name__ == '__main__':
    unittest.main()
