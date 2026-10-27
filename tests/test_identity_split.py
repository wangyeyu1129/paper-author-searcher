"""Synthetic split-profile fixtures: one person indexed under several IDs of the same source."""

import unittest
from dataclasses import replace

from paper_author_searcher import AuthorCandidate as C
from paper_author_searcher import ProfessorQuery as Q
from paper_author_searcher import ProfessorResolver
from paper_author_searcher.config import MatchingConfig
from paper_author_searcher.identity import compare_identity

SCHOOL = ['Example University']
LAB = ['Ana Ruiz', 'Ben Okafor', 'Chen Wei', 'Dana Kim', 'Eli Novak', 'Fay Osei', 'Gus Mora']
TOPICS = ['Robustness', 'Verification', 'Neural Networks']


class Provider:
    name = 'fixture'

    def __init__(self, records):
        self.records = records

    def search(self, query):
        return self.records


def fragment(author_id, coauthors=LAB, topics=TOPICS, orcid=None):
    return C(
        'openalex', author_id, 'Mei Lin', list(SCHOOL), coauthors=list(coauthors), topics=list(topics), orcid=orcid
    )


def resolve(records, config=None):
    resolver = ProfessorResolver([Provider(records)], config=config or MatchingConfig())
    return resolver.resolve(Q('Mei Lin', 'Example University'))


class SplitProfileTests(unittest.TestCase):
    def test_fragments_with_shared_lab_and_topics_merge_into_one_identity(self):
        result = resolve([fragment('A1'), fragment('A2', coauthors=LAB[1:]), fragment('A3', coauthors=LAB[:5])])
        self.assertEqual(result.profile.status, 'resolved')
        self.assertEqual(sorted(result.profile.author_id_aliases['openalex']), ['A1', 'A2', 'A3'])
        self.assertTrue(any('split profile' in item for item in result.profile.evidence) or result.ambiguity is None)

    def test_namesake_with_disjoint_collaborators_stays_separate(self):
        other = fragment('B1', coauthors=['Xu Ming', 'Yara Silva', 'Zoe Park'], topics=['Ecology', 'Soil'])
        result = resolve([fragment('A1'), other])
        self.assertEqual(result.profile.status, 'ambiguous')
        self.assertEqual(len(result.candidates), 2)

    def test_too_few_shared_coauthors_do_not_merge(self):
        link = compare_identity(fragment('A1'), fragment('A2', coauthors=LAB[:4] + ['Someone Else']))
        self.assertFalse(link.matched)

    def test_different_school_does_not_merge(self):
        moved = fragment('A2')
        moved.affiliations = ['Faraway Institute']
        self.assertFalse(compare_identity(fragment('A1'), moved).matched)

    def test_conflicting_orcids_block_the_merge(self):
        link = compare_identity(
            fragment('A1', orcid='0000-0000-0000-0001'), fragment('A2', orcid='0000-0000-0000-0002')
        )
        self.assertFalse(link.matched)

    def test_merge_can_be_disabled_by_config(self):
        config = replace(MatchingConfig(), merge_split_profiles=False)
        self.assertFalse(compare_identity(fragment('A1'), fragment('A2'), config).matched)

    def test_thresholds_come_from_config(self):
        strict = replace(MatchingConfig(), split_profile_coauthors=8)
        self.assertFalse(compare_identity(fragment('A1'), fragment('A2'), strict).matched)
        self.assertTrue(compare_identity(fragment('A1'), fragment('A2')).matched)


class AbbreviatedNameTests(unittest.TestCase):
    """Semantic Scholar often keeps only the byline form ("M. Lin") of a name another source spells out."""

    DOIS = ('10.1000/a', '10.1000/b', '10.1000/c')

    def test_an_abbreviated_name_with_shared_papers_links_to_the_full_one(self):
        full = C('dblp', 'x/1', 'Mei Lin', paper_ids=list(self.DOIS))
        byline = C('semantic_scholar', '9', 'M. Lin', paper_ids=list(self.DOIS))
        self.assertTrue(compare_identity(byline, full).matched)

    def test_an_agreeing_abbreviation_alone_is_no_link_and_a_different_initial_still_conflicts(self):
        full = C('dblp', 'x/1', 'Mei Lin', paper_ids=['10.1000/a'])
        byline = C('semantic_scholar', '9', 'M. Lin', paper_ids=['10.1000/z'])
        link = compare_identity(byline, full)
        self.assertFalse(link.matched or link.conflict)
        other = compare_identity(C('semantic_scholar', '8', 'J. Lin', paper_ids=list(self.DOIS)), full)
        self.assertTrue(other.conflict)


if __name__ == '__main__':
    unittest.main()


class CoauthorOverlapTests(unittest.TestCase):
    """Five shared names mean little between two profiles with thousands of collaborators each."""

    def test_large_consortium_profiles_do_not_merge_on_a_few_shared_names(self):
        consortium = [f'Member {index}' for index in range(1000)]
        left = fragment('A1', coauthors=LAB + consortium[:500])
        right = fragment('A2', coauthors=LAB + consortium[500:])
        link = compare_identity(left, right)
        self.assertFalse(link.split_profile)
        self.assertTrue(compare_identity(left, right, MatchingConfig(split_profile_coauthor_overlap=0)).split_profile)

    def test_lab_sized_profiles_still_merge_and_report_overlap(self):
        link = compare_identity(fragment('A1'), fragment('A2'))
        self.assertTrue(link.split_profile)
        self.assertIn('overlap', link.evidence[0])
