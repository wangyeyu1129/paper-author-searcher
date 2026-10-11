import unittest
from paper_author_searcher import AuthorCandidate as C, ProfessorQuery as Q, ProfessorResolver
from paper_author_searcher.resolver import linked


class Provider:
    name = "fixture"

    def __init__(self, records):
        self.records = records

    def search(self, query):
        return self.records


def resolve(records, name="Mei Lin", university="Example University", **kwargs):
    return ProfessorResolver([Provider(records)]).resolve(Q(name, university, **kwargs))


class ResolverTests(unittest.TestCase):
    def test_same_name_school(self):
        result = resolve([C("openalex", "wrong", "Mei Lin", ["Other University"]),
                          C("openalex", "right", "Mei Lin", ["Example University"])])
        self.assertEqual(result.profile.author_ids["openalex"], "right")
        self.assertEqual(result.profile.status, "resolved")

    def test_multi_source(self):
        result = resolve([C("openalex", "A1", "Mei Lin", ["Example University"], paper_ids=["doi:10.1/a", "doi:10.1/b"]),
                          C("semantic_scholar", "123", "Mei Lin", ["Example University"], paper_ids=["doi:10.1/a", "doi:10.1/b"])])
        self.assertEqual(len(result.profile.author_ids), 2)
        self.assertGreaterEqual(result.profile.identity_confidence, 0.89)

    def test_ambiguous(self):
        result = resolve([C("openalex", str(i), "Mei Lin", ["Example University"]) for i in range(2)])
        self.assertEqual(result.profile.status, "ambiguous")
        self.assertLess(result.profile.identity_confidence, 0.7)

    def test_unknown_affiliation(self):
        self.assertEqual(resolve([C("dblp", "x", "Mei Lin")]).profile.status, "needs_review")

    def test_wrong_school(self):
        self.assertLess(resolve([C("openalex", "x", "Mei Lin", ["Northern University"])]).profile.identity_confidence, 0.5)

    def test_alias_and_unicode(self):
        result = resolve([C("openalex", "A", "Jose Silva", ["EIT"])], name="José Silva",
                         university="Example Institute of Technology", university_aliases=["EIT"])
        self.assertEqual(result.profile.status, "resolved")

    def test_initial_requires_review(self):
        self.assertEqual(resolve([C("openalex", "x", "M. Lin", ["Example University"])]).profile.status, "needs_review")

    def test_conflicting_orcid_not_merged(self):
        self.assertFalse(linked(C("openalex", "a", "Mei Lin", ["EXU"], orcid="0001"),
                                C("dblp", "b", "Mei Lin", ["EXU"], orcid="0002")))

    def test_name_only_never_merges(self):
        self.assertFalse(linked(C("openalex", "a", "Mei Lin"), C("dblp", "b", "Mei Lin")))

    def test_empty(self):
        self.assertIsNone(resolve([]).profile)

    def test_source_failure_partial_result(self):
        class Broken:
            name = "broken"
            def search(self, query):
                raise RuntimeError("rate limited")
        result = ProfessorResolver([Broken(), Provider([C("openalex", "a", "Mei Lin", ["EXU"])])]).resolve(Q("Mei Lin", "EXU"))
        self.assertIn("broken", result.source_errors)
        self.assertEqual(result.profile.status, "resolved")

    def test_invalid_query(self):
        with self.assertRaises(ValueError):
            Q("", "EXU")
