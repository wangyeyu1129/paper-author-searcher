"""Split-profile merging: fragment selection in the resolver and coauthor-network merging in the collector."""

import unittest
from dataclasses import replace

from paper_author_searcher.config import DEFAULT_FRAGMENTS
from paper_author_searcher.fragment_merging import Fragment, FragmentMerger, person_coauthors, split_fragments
from paper_author_searcher.models import AuthorCandidate, PaperRecord, PaperSourceResult, ProfessorProfile
from paper_author_searcher.paper_collector import PaperCollector

ORCID = '0000-0002-1825-0097'
SCHOOL = 'Example University'


def profile(**changes):
    values = {
        'name': 'Mei Lin',
        'affiliations': [SCHOOL],
        'author_ids': {'openalex': 'A1'},
        'author_id_aliases': {'openalex': ['A1']},
        'identity_confidence': 0.95,
        'status': 'resolved',
        'evidence': [],
        'requested_name': 'Mei Lin',
        'requested_university': SCHOOL,
        'matched_affiliations': [SCHOOL],
        'university': SCHOOL,
    }
    values.update(changes)
    return ProfessorProfile(**values)


def record(identifier, authors, author_id='A1', source='openalex'):
    return PaperRecord(
        title=f'Paper {identifier} about graphs',
        authors=list(authors),
        year=2024,
        doi=f'10.1000/{identifier}',
        venue=None,
        abstract=None,
        source=source,
        source_paper_id=identifier,
        source_author_id=author_id,
        paper_url=None,
    )


def fragment(author_id, coauthors, affiliations=(SCHOOL,), orcid=None, source='openalex'):
    return Fragment(source, author_id, tuple(sorted(coauthors)), (), tuple(affiliations), orcid)


class FragmentSelectionTests(unittest.TestCase):
    def test_compatible_candidates_from_merge_sources_become_fragments_best_name_first(self):
        groups = [
            [AuthorCandidate('openalex', 'A2', 'Mei Lin', affiliations=[SCHOOL], coauthors=['Ana Other', 'Mei Lin'])],
            [AuthorCandidate('openalex', 'A3', 'Wei Zhang')],
            [AuthorCandidate('dblp', 'x/1', 'Mei Lin')],
            [AuthorCandidate('openalex', 'A4', 'Mei Lin', orcid='0000-0001-0000-0001')],
            [AuthorCandidate('semantic_scholar', '9', 'M. Lin')],
        ]
        found = split_fragments(profile(orcid=ORCID), groups)
        self.assertEqual([(item.source, item.author_id) for item in found], [('openalex', 'A2')])
        self.assertEqual(found[0].coauthors, ('ana other',))
        self.assertEqual(Fragment.from_dict(found[0].to_dict()), found[0])
        limited = split_fragments(profile(), groups, replace(DEFAULT_FRAGMENTS, maximum_fragments=0))
        self.assertEqual(limited, [])


class MergerTests(unittest.TestCase):
    merger = FragmentMerger()

    def verified(self):
        return [record('p1', ['Mei Lin', 'Rare Collaborator', 'Common Name']), record('p2', ['Mei Lin', 'Second Rare'])]

    def test_distinctive_shared_coauthors_and_the_institution_merge_a_fragment(self):
        decisions = self.merger.select(
            profile(), self.verified(), [fragment('A2', {'rare collaborator', 'second rare', 'someone'})]
        )
        self.assertTrue(decisions[0].accepted)
        self.assertEqual(decisions[0].shared_coauthors, ['rare collaborator', 'second rare'])
        self.assertEqual(decisions[0].score, 2.0)
        self.assertIn('Split profile openalex:A2 merged', decisions[0].evidence())

    def test_coauthors_common_to_many_fragments_weigh_little(self):
        crowd = [fragment(f'N{index}', {'common name', 'second rare'}, ('Elsewhere',)) for index in range(6)]
        target = fragment('A2', {'common name', 'second rare'})
        decision = next(
            item for item in self.merger.select(profile(), self.verified(), [*crowd, target]) if item.fragment == target
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, 'too little shared coauthor evidence')

    def test_another_institution_or_orcid_blocks_a_merge(self):
        coauthors = {'rare collaborator', 'second rare'}
        elsewhere = self.merger.select(profile(), self.verified(), [fragment('A2', coauthors, ('Other College',))])
        self.assertEqual(elsewhere[0].reason, 'no shared institution')
        other = self.merger.select(
            profile(orcid=ORCID), self.verified(), [fragment('A2', coauthors, orcid='0000-0001-0000-0001')]
        )
        self.assertEqual(other[0].reason, 'another ORCID')
        anywhere = FragmentMerger(replace(DEFAULT_FRAGMENTS, require_institution=False))
        self.assertTrue(anywhere.select(profile(), self.verified(), [fragment('A2', coauthors, ())])[0].accepted)

    def test_fragments_linked_only_through_another_fragment_merge_by_closure(self):
        first = fragment('A2', {'rare collaborator', 'second rare', 'bridge one', 'bridge two'})
        second = fragment('A3', {'bridge one', 'bridge two'})
        decisions = self.merger.select(profile(), self.verified(), [second, first])
        self.assertEqual([item.fragment.author_id for item in decisions if item.accepted], ['A2', 'A3'])

    def test_an_unenriched_fragment_gets_its_evidence_from_its_own_papers(self):
        paper = record('p9', ['Mei Lin', 'Rare Collaborator', 'Second Rare'], author_id='https://openalex.org/A2')
        paper.author_details = [
            {'name': 'Mei Lin', 'author_id': 'https://openalex.org/A2', 'institutions': [{'display_name': SCHOOL}]},
            {
                'name': 'Rare Collaborator',
                'author_id': 'https://openalex.org/A7',
                'institutions': [{'display_name': 'X'}],
            },
        ]
        bare = fragment('A2', set(), ())
        papered = bare.with_papers([paper], ['Mei Lin'])
        self.assertEqual(papered.coauthors, ('rare collaborator', 'second rare'))
        self.assertEqual(papered.affiliations, (SCHOOL,))
        self.assertTrue(self.merger.select(profile(), self.verified(), [papered])[0].accepted)

    def test_the_persons_own_names_are_not_coauthors(self):
        self.assertEqual(person_coauthors(profile(), [record('p', ['Mei Lin', 'Ana Other'])]), {'ana other'})


class Provider:
    def __init__(self, name, papers):
        self.name, self.papers, self.collected = name, papers, []

    def collect(self, author_id):
        self.collected.append(author_id)
        result = PaperSourceResult(source=self.name, author_id=author_id, complete=True, pagination_complete=True)
        result.papers = list(self.papers.get(author_id, []))
        return result


class Verifier:
    """Verifies every record of the selected identity; the merger's evidence is what it sees verified."""

    def validate(self, papers, profile):
        for paper in papers:
            selected = paper.source_author_id in profile.author_id_aliases.get(paper.source, [])
            paper.ownership_status = 'verified' if selected else 'needs_review'
        return papers


class CollectorTests(unittest.TestCase):
    def test_merged_fragments_are_collected_and_join_the_selected_identity(self):
        provider = Provider(
            'openalex',
            {
                'A1': [record('p1', ['Mei Lin', 'Rare Collaborator', 'Second Rare'])],
                'A2': [record('p9', ['Mei Lin', 'Rare Collaborator'], author_id='A2')],
                'A5': [record('p5', ['Mei Lin', 'Stranger'], author_id='A5')],
            },
        )
        fragments = [
            fragment('A2', {'rare collaborator', 'second rare'}).to_dict(),
            fragment('A5', {'x', 'y'}).to_dict(),
        ]
        collector = PaperCollector([provider], Verifier(), fragment_merger=FragmentMerger())
        result = collector.collect(profile(split_fragments=fragments))
        # Every fragment is paged for its evidence; only the accepted one joins, with its papers.
        self.assertEqual(provider.collected, ['A1', 'A2', 'A5'])
        self.assertEqual(result.selected_author_ids['openalex'], ['A1', 'A2'])
        self.assertEqual({paper.source_paper_id for paper in result.papers}, {'p1', 'p9'})

    def test_fragments_that_cannot_share_the_institution_are_never_paged(self):
        provider = Provider('openalex', {'A1': [record('p1', ['Mei Lin'])]})
        collector = PaperCollector([provider], Verifier(), fragment_merger=FragmentMerger())
        unknown = fragment('A2', {'x', 'y'}, ())
        elsewhere = fragment('A3', {'x', 'y'}, ('Other College',))
        collector.collect(profile(split_fragments=[unknown.to_dict(), elsewhere.to_dict()]))
        self.assertEqual(provider.collected, ['A1'])
        anywhere = FragmentMerger(replace(DEFAULT_FRAGMENTS, require_institution=False))
        self.assertEqual(anywhere.worth_paging(profile(), [unknown, elsewhere]), [unknown, elsewhere])

    def test_no_fragments_means_no_extra_collection(self):
        provider = Provider('openalex', {'A1': [record('p1', ['Mei Lin'])]})
        collector = PaperCollector([provider], Verifier(), fragment_merger=FragmentMerger())
        collector.collect(profile(split_fragments=[fragment('A2', {'x', 'y'}, source='dblp').to_dict()]))
        self.assertEqual(provider.collected, ['A1'])


if __name__ == '__main__':
    unittest.main()
