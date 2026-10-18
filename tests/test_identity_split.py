"""Synthetic split-profile fixtures: one person indexed under several IDs of the same source."""
import unittest
from dataclasses import replace

from paper_author_searcher import AuthorCandidate as C, ProfessorQuery as Q, ProfessorResolver
from paper_author_searcher.config import MatchingConfig
from paper_author_searcher.identity import compare_identity

SCHOOL = ["Example University"]
LAB = ["Ana Ruiz", "Ben Okafor", "Chen Wei", "Dana Kim", "Eli Novak", "Fay Osei", "Gus Mora"]
TOPICS = ["Robustness", "Verification", "Neural Networks"]


class Provider:
    name = "fixture"

    def __init__(self, records):
        self.records = records

    def search(self, query):
        return self.records


def fragment(author_id, coauthors=LAB, topics=TOPICS, orcid=None):
    return C("openalex", author_id, "Mei Lin", list(SCHOOL), coauthors=list(coauthors), topics=list(topics),
             orcid=orcid)


def resolve(records, config=MatchingConfig()):
    return ProfessorResolver([Provider(records)], config=config).resolve(Q("Mei Lin", "Example University"))


class SplitProfileTests(unittest.TestCase):
    def test_fragments_with_shared_lab_and_topics_merge_into_one_identity(self):
        result = resolve([fragment("A1"), fragment("A2", coauthors=LAB[1:]), fragment("A3", coauthors=LAB[:5])])
        self.assertEqual(result.profile.status, "resolved")
        self.assertEqual(sorted(result.profile.author_id_aliases["openalex"]), ["A1", "A2", "A3"])
        self.assertTrue(any("split profile" in item for item in result.profile.evidence) or result.ambiguity is None)

    def test_namesake_with_disjoint_collaborators_stays_separate(self):
        other = fragment("B1", coauthors=["Xu Ming", "Yara Silva", "Zoe Park"], topics=["Ecology", "Soil"])
        result = resolve([fragment("A1"), other])
        self.assertEqual(result.profile.status, "ambiguous")
        self.assertEqual(len(result.candidates), 2)

    def test_too_few_shared_coauthors_do_not_merge(self):
        link = compare_identity(fragment("A1"), fragment("A2", coauthors=LAB[:4] + ["Someone Else"]))
        self.assertFalse(link.matched)

    def test_different_school_does_not_merge(self):
        moved = fragment("A2")
        moved.affiliations = ["Faraway Institute"]
        self.assertFalse(compare_identity(fragment("A1"), moved).matched)

    def test_conflicting_orcids_block_the_merge(self):
        link = compare_identity(fragment("A1", orcid="0000-0000-0000-0001"),
                                fragment("A2", orcid="0000-0000-0000-0002"))
        self.assertFalse(link.matched)

    def test_merge_can_be_disabled_by_config(self):
        config = replace(MatchingConfig(), merge_split_profiles=False)
        self.assertFalse(compare_identity(fragment("A1"), fragment("A2"), config).matched)

    def test_thresholds_come_from_config(self):
        strict = replace(MatchingConfig(), split_profile_coauthors=8)
        self.assertFalse(compare_identity(fragment("A1"), fragment("A2"), strict).matched)
        self.assertTrue(compare_identity(fragment("A1"), fragment("A2")).matched)


if __name__ == "__main__":
    unittest.main()
