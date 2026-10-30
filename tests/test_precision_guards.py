"""Common-name precision guards, on synthetic Tsinghua-style namesake fragments."""

import dataclasses
import unittest

from paper_author_searcher.config import PrecisionGuardConfig
from paper_author_searcher.models import AnchorEvidence, PaperRecord, ProfessorProfile
from paper_author_searcher.paper_deduplication import deduplicate_papers
from paper_author_searcher.paper_ownership import PaperOwnershipValidator
from paper_author_searcher.precision_guards import (
    ActiveGuard,
    CommonNamePolicy,
    DepartmentMatcher,
    GuardContext,
    PrecisionGuard,
)
from paper_author_searcher.status import AnchorKind

CS = 'Department of Computer Science and Technology, Tsinghua University, Beijing'
VEHICLE = 'School of Vehicle and Mobility, Tsinghua University, Beijing'


def record(
    title, author_id, affiliation, coauthors=('Jane Smith', 'John Green'), doi=None, institution='Tsinghua University'
):
    detail = {
        'author_id': author_id,
        'name': 'Yang Liu',
        'role': 'author',
        'institutions': [{'display_name': institution}] if institution else [],
        'affiliations': [{'raw_affiliation_string': affiliation}] if affiliation else [],
    }
    return PaperRecord(
        title=title,
        authors=['Yang Liu', *coauthors],
        year=2024,
        doi=doi or '10.1000/' + title.lower().replace(' ', '-'),
        venue='Venue',
        abstract=None,
        source='openalex',
        source_paper_id='W:' + title,
        source_author_id=author_id,
        paper_url=None,
        publication_type='article',
        author_details=[detail],
    )


def profile(anchors=(), name='Yang Liu', department='Computer Science and Technology'):
    return ProfessorProfile(
        name=name,
        affiliations=['Tsinghua University'],
        author_ids={'openalex': 'A1'},
        identity_confidence=0.9,
        status='resolved',
        evidence=[],
        author_id_aliases={'openalex': ['A1', 'B1']},
        university='Tsinghua University',
        matched_affiliations=['Tsinghua University'],
        requested_university='Tsinghua University',
        identity_anchors=list(anchors),
        requested_name=name,
        requested_department=department,
    )


def own_anchor(author_id='A1', title='Own Paper', kind=AnchorKind.PAPER):
    return AnchorEvidence(
        'openalex', author_id, 'author of DOI', kind, 'doi:10.1000/' + title.lower().replace(' ', '-')
    )


def corpus():
    """Fragment A1: the person's CS papers. Fragment B1: a vehicle-engineering namesake under which OpenAlex also
    filed one of the person's own papers, so a homepage anchor lands on it."""
    own = [record('Own Paper', 'A1', CS), *(record(f'CS Paper {index}', 'A1', CS) for index in range(4))]
    misfiled = record('Misfiled Own Paper', 'B1', CS)
    namesake = [record(f'Traffic Paper {index}', 'B1', VEHICLE, ('Peter Brown', 'Mary White')) for index in range(6)]
    return [*own, misfiled, *namesake]


def statuses(records, target, guard):
    papers = deduplicate_papers(records)
    PaperOwnershipValidator(guard=guard).validate(papers, target)
    return {paper.title: paper.ownership_status for paper in papers}


class PolicyTests(unittest.TestCase):
    def test_common_names_come_from_the_surname_list(self):
        names = CommonNamePolicy.from_config(PrecisionGuardConfig())
        self.assertTrue(names.is_common('Yang Liu'))
        self.assertTrue(names.is_common('Liu Yang'))
        self.assertFalse(names.is_common('Raj Chetty'))
        self.assertTrue(names.collides_easily('Hui Li', 4))
        self.assertFalse(names.collides_easily('Zhouxing Shi', 4))

    def test_department_phrases_and_agreement(self):
        departments = DepartmentMatcher()
        self.assertEqual(departments.phrases(CS), ['department of computer science and technology'])
        self.assertTrue(
            departments.agree('school of computer science and technology', 'Computer Science and Technology')
        )
        self.assertFalse(departments.agree('school of vehicle and mobility', 'Computer Science and Technology'))

    def test_uncommon_names_keep_the_ordinary_rules(self):
        context = PrecisionGuard().prepare(deduplicate_papers(corpus()), profile(name='Raj Chetty'))
        self.assertIs(type(context), GuardContext)


class GuardTests(unittest.TestCase):
    def test_namesake_papers_need_the_persons_unit_not_just_the_university(self):
        target = profile([own_anchor(), own_anchor('B1', 'Misfiled Own Paper')])
        context = PrecisionGuard().prepare(deduplicate_papers(corpus()), target)
        self.assertIsInstance(context, ActiveGuard)
        self.assertIn('department of computer science and technology', context.target_departments)
        guarded = statuses(corpus(), target, PrecisionGuard())
        ordinary = statuses(corpus(), target, None)
        self.assertEqual(ordinary['Traffic Paper 3'], 'verified')  # Tsinghua matches at university level
        self.assertEqual(guarded['Traffic Paper 3'], 'needs_review')
        self.assertEqual(guarded['CS Paper 1'], 'verified')
        self.assertEqual(guarded['Misfiled Own Paper'], 'verified')  # its own unit agrees, so it is claimed alone

    def test_foreign_fragment_papers_are_claimed_one_by_one_only(self):
        # Mostly another unit: even a paper naming no unit (neutral at record level) is not taken wholesale.
        records = [*corpus(), *(record(f'Road Paper {index}', 'B1', VEHICLE, ('Peter Brown',)) for index in range(6))]
        records.append(record('Unitless Paper', 'B1', None, ('Peter Brown',)))
        target = profile([own_anchor(), own_anchor('B1', 'Misfiled Own Paper')])
        context = PrecisionGuard().prepare(deduplicate_papers(records), target)
        self.assertTrue(context.foreign(records[-1]))
        self.assertEqual(statuses(records, target, None)['Unitless Paper'], 'verified')
        self.assertEqual(statuses(records, target, PrecisionGuard())['Unitless Paper'], 'needs_review')

    def test_orcid_anchored_fragments_are_not_foreign_but_their_papers_keep_the_unit_check(self):
        # The ORCID is the cluster's: it may also hold a same-school namesake's papers, which name their own unit.
        target = profile([own_anchor(), AnchorEvidence('openalex', 'B1', 'ORCID 0000-0001', AnchorKind.ORCID)])
        records = [*corpus(), record('Unitless Paper', 'B1', None, ('Peter Brown',))]
        context = PrecisionGuard().prepare(deduplicate_papers(records), target)
        self.assertFalse(context.foreign(records[-1]))
        guarded = statuses(records, target, PrecisionGuard())
        self.assertEqual(guarded['Traffic Paper 3'], 'needs_review')
        self.assertEqual(guarded['Unitless Paper'], 'verified')
        self.assertEqual(guarded['Misfiled Own Paper'], 'verified')

    def test_unit_exempt_anchor_kinds_restore_the_exemption(self):
        target = profile([own_anchor(), AnchorEvidence('openalex', 'B1', 'ORCID 0000-0001', AnchorKind.ORCID)])
        guard = PrecisionGuard(PrecisionGuardConfig(unit_exempt_anchor_kinds=('orcid',)))
        self.assertEqual(statuses(corpus(), target, guard)['Traffic Paper 3'], 'verified')

    def test_the_guard_is_configurable(self):
        target = profile([own_anchor(), own_anchor('B1', 'Misfiled Own Paper')])
        disabled = PrecisionGuard(PrecisionGuardConfig(enabled=False))
        self.assertEqual(statuses(corpus(), target, disabled)['Traffic Paper 3'], 'verified')

    def test_without_department_evidence_checks_stay_neutral(self):
        # No anchor work in the collection: the requested department alone must not block papers.
        target = profile([AnchorEvidence('openalex', 'A1', 'author of DOI', AnchorKind.PAPER, 'doi:10.1000/elsewhere')])
        self.assertEqual(statuses(corpus(), target, PrecisionGuard())['Traffic Paper 3'], 'verified')

    def test_easily_colliding_coauthors_count_only_when_on_the_persons_own_papers(self):
        target = profile([own_anchor()])
        guard = PrecisionGuard().prepare(deduplicate_papers(corpus()), target)
        candidate = record('Other', 'A1', CS, coauthors=('Hui Li', 'Jane Smith'))
        self.assertFalse(guard.counts_as_coauthor(candidate, 'Hui Li'))
        self.assertTrue(guard.counts_as_coauthor(candidate, 'Jane Smith'))  # on the own (anchor) paper

    def test_contradicting_institution_blocks_upgrades_unless_the_persons_history_has_it(self):
        target = profile([own_anchor()])
        stanford = record('Visiting Paper', 'A1', None, institution='Stanford University')
        guard = PrecisionGuard().prepare(deduplicate_papers([*corpus(), stanford]), target)
        self.assertTrue(guard.contradicted(stanford, stanford.author_details))
        history = record('Own Paper', 'A1', CS, institution='Stanford University')
        records = [history, *corpus()[1:], stanford]
        guard = PrecisionGuard().prepare(deduplicate_papers(records), target)
        self.assertFalse(guard.contradicted(stanford, stanford.author_details))

    def test_thresholds_are_validated(self):
        with self.assertRaises(ValueError):
            PrecisionGuardConfig(fragment_minimum_share=2)
        self.assertEqual(
            dataclasses.replace(PrecisionGuardConfig(), fragment_minimum_share=0.5).fragment_minimum_share, 0.5
        )


if __name__ == '__main__':
    unittest.main()


class AffiliationSplitTests(unittest.TestCase):
    def test_first_part_is_the_institution_even_with_a_unit_keyword(self):
        institutions, units = DepartmentMatcher().split(
            'Massachusetts Institute of Technology, Department of Electrical Engineering and Computer Science, USA'
        )
        self.assertEqual(institutions, ['Massachusetts Institute of Technology'])
        self.assertEqual(units, ['department of electrical engineering and computer science'])

    def test_without_a_unit_every_leading_prefix_is_a_candidate_name(self):
        self.assertEqual(
            DepartmentMatcher().split('Princeton University, NJ, USA'),
            (['Princeton University', 'Princeton University, NJ', 'Princeton University, NJ, USA'], []),
        )

    def test_empty_affiliation(self):
        self.assertEqual(DepartmentMatcher().split(' , '), ([], []))
