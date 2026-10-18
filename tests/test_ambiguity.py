import unittest

from paper_author_searcher import AuthorCandidate, ProfessorQuery, ProfessorResolver
from paper_author_searcher.cli import summary
from test_resolver import Provider


class AmbiguityTests(unittest.TestCase):
    def resolve(self, records):
        query = ProfessorQuery('Alex Morgan', 'Example University')
        return ProfessorResolver([Provider(records)]).resolve(query)

    def test_duplicate_cross_source_with_missing_school_resolves(self):
        result = self.resolve(
            [
                AuthorCandidate('first', 'a', 'Alex Morgan', ['Example University'], orcid='shared'),
                AuthorCandidate('second', 'b', 'Alex Morgan', orcid='shared'),
            ]
        )
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.profile.status, 'resolved')
        self.assertIsNone(result.ambiguity)

    def test_same_source_verified_duplicates_preserve_all_ids(self):
        result = self.resolve(
            [
                AuthorCandidate('first', 'a', 'Alex Morgan', ['Example University'], orcid='shared'),
                AuthorCandidate('first', 'b', 'Alex Morgan', orcid='shared'),
            ]
        )
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.profile.author_id_aliases['first'], ['a', 'b'])
        self.assertEqual(result.profile.author_ids['first'], 'a')

    def test_true_namesakes_report_raw_scores_and_reason(self):
        result = self.resolve(
            [
                AuthorCandidate('first', 'a', 'Alex Morgan', ['Example University'], orcid='left'),
                AuthorCandidate('second', 'b', 'Alex Morgan', ['Example University'], orcid='right'),
            ]
        )
        self.assertEqual(result.profile.status, 'ambiguous')
        self.assertGreater(result.profile.match_score, result.profile.identity_confidence)
        self.assertIn('Conflicting ORCID identifiers', result.ambiguity['reasons'])
        self.assertEqual(len(result.ambiguity['contenders']), 2)
        self.assertIn('ambiguity', summary(result))
        self.assertEqual(result.ambiguity['score_margin'], 0)

    def test_unverified_missing_school_never_resolves(self):
        result = self.resolve(
            [
                AuthorCandidate('first', 'a', 'Alex Morgan'),
                AuthorCandidate('second', 'b', 'Alex Morgan'),
            ]
        )
        self.assertEqual(result.profile.status, 'needs_review')
        self.assertEqual(len(result.candidates), 2)

    def test_school_output_uses_observed_label(self):
        result = self.resolve(
            [
                AuthorCandidate('first', 'a', 'Alex Morgan', ['University of Example']),
            ]
        )
        self.assertEqual(result.profile.requested_university, 'Example University')
        self.assertEqual(result.profile.university, 'University of Example')
        self.assertEqual(result.profile.matched_affiliations, ['University of Example'])

    def test_shared_dois_merge_cross_source_duplicate(self):
        result = self.resolve(
            [
                AuthorCandidate(
                    'first', 'a', 'Alex Morgan', ['Example University'], paper_ids=['doi:example/a', 'doi:example/b']
                ),
                AuthorCandidate('second', 'b', 'Alex Morgan', paper_ids=['doi:example/a', 'doi:example/b']),
            ]
        )
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.profile.status, 'resolved')

    def test_same_source_duplicates_do_not_inflate_score(self):
        first = AuthorCandidate('first', 'a', 'Alex Morgan', ['Example University'], orcid='shared')
        single = self.resolve([first])
        duplicate = self.resolve(
            [
                first,
                AuthorCandidate('first', 'b', 'Alex Morgan', orcid='shared'),
            ]
        )
        self.assertEqual(single.profile.match_score, duplicate.profile.match_score)

    def test_raw_score_difference_survives_confidence_cap(self):
        result = self.resolve(
            [
                AuthorCandidate(
                    'first', 'a', 'Alex Morgan', ['Example University'], ['Example University'], orcid='left'
                ),
                AuthorCandidate('second', 'b', 'Alex Morgan', ['Example University'], orcid='right'),
            ]
        )
        contenders = result.ambiguity['contenders']
        self.assertEqual(contenders[0]['match_score'], 0.87)
        self.assertEqual(contenders[1]['match_score'], 0.85)
        self.assertEqual(result.ambiguity['score_margin'], 0.02)
        self.assertEqual(result.profile.identity_confidence, 0.69)
