import unittest
from dataclasses import replace

from paper_author_searcher import AuthorCandidate, ProfessorQuery, ProfessorResolver
from paper_author_searcher.byline_disambiguation import BylineDisambiguator, PositionFit
from paper_author_searcher.config import DEFAULT_DISAMBIGUATION
from paper_author_searcher.http import SourceError
from paper_author_searcher.identity_anchors import IdentityAnchors
from paper_author_searcher.models import CuratedPosition, ProfessorProfile
from paper_author_searcher.status import AnchorKind
from test_resolver import Provider

QUERY = ProfessorQuery('Alex Morgan', 'Example University', department='Materials Science and Engineering')
HOME = 'Department of Materials Science and Engineering, Example University, Springfield'
MEDICINE = 'Department of Medicine, Example University'
SCHOOL = 'School of Engineering and Applied Sciences, Example University'


def contender(name, score, group):
    profile = ProfessorProfile(name, [], {}, score, 'ambiguous', [], match_score=score, university_status='matched')
    return profile, list(group)


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


class CuratedPositionTests(unittest.TestCase):
    QUERY = ProfessorQuery('Yang Liu', 'Tsinghua University', department='Computer Science and Technology')

    def contest(self, *groups):
        return [contender('Yang Liu', 0.9, group) for group in groups]

    @staticmethod
    def dblp(author_id, *positions, bylines=()):
        candidate = AuthorCandidate('dblp', author_id, 'Yang Liu', ['Tsinghua University'])
        candidate.positions = list(positions)
        candidate.bylines = [(line,) for line in bylines]
        return candidate

    @staticmethod
    def at(unit, current=True, institution='Tsinghua University', source='dblp'):
        return CuratedPosition(source, (institution,), (unit,) if unit else (), current)

    def choose(self, *groups, orcid_positions=None, config=DEFAULT_DISAMBIGUATION):
        return BylineDisambiguator(config, orcid_positions=orcid_positions).choose(self.QUERY, self.contest(*groups))

    def test_the_only_record_in_the_requested_department_wins(self):
        verdict = self.choose(
            [self.dblp('51/3710-165', self.at('institute for ai industry research'))],
            [self.dblp('51/3710-5', self.at('department of computer science and technology'))],
        )
        self.assertEqual(verdict.index, 1)
        self.assertIn('Curated position', verdict.note)

    def test_a_department_record_behind_the_band_still_competes(self):
        institute = self.dblp('51/3710-165', self.at('institute for ai industry research'))
        other = self.dblp('49576068')
        behind = self.dblp('51/3710-5', self.at('department of computer science and technology'))
        unrelated = self.dblp('0001')
        scored = [
            contender('Yang Liu', 0.935, [institute]),
            contender('Yang Liu', 0.913, [other]),
            contender('Yang Liu', 0.87, [behind]),
            contender('Yang Liu', 0.80, [self.dblp('far', self.at('department of computer science and technology'))]),
            contender('Yang Liu', 0.87, [unrelated]),
        ]
        verdict = BylineDisambiguator().choose(self.QUERY, scored)
        # 0.87 is outside the 0.05 contest band, inside the 0.1 curated band; 0.80 is outside both.
        self.assertEqual(verdict.index, 2)

    def test_two_records_in_the_department_leave_the_contest_open(self):
        unit = 'department of computer science and technology'
        self.assertIsNone(self.choose([self.dblp('a', self.at(unit))], [self.dblp('b', self.at(unit))]))

    def test_past_positions_and_other_schools_do_not_count(self):
        unit = 'department of computer science and technology'
        self.assertIsNone(
            self.choose(
                [self.dblp('a', self.at(unit, current=False))],
                [self.dblp('b', self.at(unit, institution='Peking University'))],
            )
        )

    def test_a_record_naming_no_unit_neither_fits_nor_rules_out(self):
        self.assertIsNone(self.choose([self.dblp('a', self.at(''))], [self.dblp('b')]))

    def test_strong_bylines_for_a_rival_veto_the_record(self):
        home = 'Department of Computer Science and Technology, Tsinghua University'
        verdict = self.choose(
            [self.dblp('a', bylines=[home] * 5)],
            [self.dblp('b', self.at('department of computer science and technology'), bylines=[home] * 2)],
        )
        # 5 vs 2 is short of the 3x needed to settle on bylines, but enough to distrust the record.
        self.assertIsNone(verdict)

    def test_thin_rival_bylines_do_not_veto_the_record(self):
        home = 'Department of Computer Science and Technology, Tsinghua University'
        verdict = self.choose(
            [self.dblp('a', bylines=[home] * 3)],
            [self.dblp('b', self.at('department of computer science and technology'))],
        )
        self.assertEqual(verdict.index, 1)

    def test_orcid_records_are_read_for_contenders(self):
        holder = self.dblp('a')
        holder.orcid = 'https://orcid.org/0000-0003-2217-6982'
        other = self.dblp('b')
        other.orcid = '0000-0001-8873-8058'
        records = {
            '0000-0003-2217-6982': [self.at('Computer Science and Technology', source='orcid')],
            '0000-0001-8873-8058': [self.at('Speech Lab', current=False, source='orcid')],
        }
        verdict = self.choose([holder], [other], orcid_positions=records.__getitem__)
        self.assertEqual(verdict.index, 0)

    def test_an_unreadable_rival_record_abstains(self):
        holder = self.dblp('a', self.at('department of computer science and technology'))
        rival = self.dblp('b')
        rival.orcid = '0000-0001-8873-8058'

        def unreadable(orcid):
            raise SourceError('HTTP 500')

        self.assertIsNone(self.choose([holder], [rival], orcid_positions=unreadable))

    def test_too_many_orcid_records_abstain(self):
        holder = self.dblp('a', self.at('department of computer science and technology'))
        rival = self.dblp('b')
        rival.orcid = '0000-0001-8873-8058'
        config = replace(DEFAULT_DISAMBIGUATION, maximum_orcid_lookups=0)
        self.assertIsNone(self.choose([holder], [rival], orcid_positions=lambda orcid: [], config=config))


class BroaderUnitTests(unittest.TestCase):
    QUERY = ProfessorQuery('Jia Liu', 'Harvard University', department='Bioengineering')

    def fit(self, unit):
        position = CuratedPosition('orcid', ('Harvard University',), (unit,), current=True)
        return BylineDisambiguator().fit(position, self.QUERY)

    def test_a_related_school_is_a_broader_unit(self):
        self.assertEqual(self.fit('School of Engineering and Applied Sciences'), PositionFit.BROADER_UNIT)

    def test_an_unrelated_school_does_not_fit(self):
        self.assertEqual(self.fit('Harvard Medical School'), PositionFit.NONE)

    def test_the_department_itself_fits_best(self):
        self.assertEqual(self.fit('Department of Bioengineering'), PositionFit.DEPARTMENT)

    def test_a_department_holder_outranks_a_broader_unit(self):
        query = self.QUERY
        broader = AuthorCandidate('orcid', 'a', 'Jia Liu')
        broader.positions = [CuratedPosition('orcid', ('Harvard University',), ('School of Engineering',), True)]
        exact = AuthorCandidate('orcid', 'b', 'Jia Liu')
        exact.positions = [CuratedPosition('orcid', ('Harvard University',), ('Department of Bioengineering',), True)]
        contest = [contender('Jia Liu', 0.87, [broader]), contender('Jia Liu', 0.85, [exact])]
        self.assertEqual(BylineDisambiguator().choose(query, contest).index, 1)
