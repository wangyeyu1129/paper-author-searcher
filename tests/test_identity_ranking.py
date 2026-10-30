"""When a close identity contest counts as settled."""

import dataclasses
import unittest

from paper_author_searcher.config import DEFAULT_MATCHING
from paper_author_searcher.identity_ranking import IdentityRanking
from paper_author_searcher.models import AnchorEvidence, ProfessorProfile
from paper_author_searcher.status import AnchorKind


def profile(score, department=None, school='matched', anchored=False):
    anchors = [AnchorEvidence('openalex', 'A1', 'author of DOI', AnchorKind.PAPER, 'doi:10.1/x')] if anchored else []
    return ProfessorProfile(
        'Yang Liu',
        [],
        {},
        score,
        'resolved',
        [],
        match_score=score,
        university_status=school,
        requested_department=department,
        identity_anchors=anchors,
    )


class MarginTests(unittest.TestCase):
    def test_a_margin_settles_a_contest_when_a_department_was_asked_for(self):
        ranking = IdentityRanking()
        self.assertTrue(ranking.uncontested(profile(0.93, 'Computer Science'), profile(0.898, 'Computer Science')))
        self.assertFalse(ranking.uncontested(profile(0.91, 'Computer Science'), profile(0.898, 'Computer Science')))

    def test_without_a_department_same_school_namesakes_need_more_than_a_margin(self):
        ranking = IdentityRanking()
        self.assertFalse(ranking.uncontested(profile(0.93), profile(0.898)))
        # Other evidence still settles it, and a namesake elsewhere is no contest.
        self.assertTrue(ranking.uncontested(profile(0.93, anchored=True), profile(0.898)))
        self.assertTrue(ranking.uncontested(profile(0.93), profile(0.6, school='mismatch')))

    def test_the_rule_is_configurable(self):
        ranking = IdentityRanking(dataclasses.replace(DEFAULT_MATCHING, margin_requires_department=False))
        self.assertTrue(ranking.uncontested(profile(0.93), profile(0.898)))


if __name__ == '__main__':
    unittest.main()
