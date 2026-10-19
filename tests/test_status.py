import inspect
import json
import unittest
from collections import Counter

from paper_author_searcher import status
from paper_author_searcher.config import AMBIGUITY_POLICIES, AmbiguityPolicy
from paper_author_searcher.status import OwnershipStatus, StrEnum

VOCABULARIES = [
    member
    for _, member in inspect.getmembers(status, inspect.isclass)
    if issubclass(member, StrEnum) and member is not StrEnum
]


class StatusCompatibilityTests(unittest.TestCase):
    """Saved reports hold plain strings; enum members must be interchangeable with them."""

    def test_members_behave_like_their_values(self):
        member = OwnershipStatus.VERIFIED
        self.assertEqual(member, 'verified')
        self.assertEqual(str(member), 'verified')
        self.assertEqual(f'{member}', 'verified')
        self.assertEqual(hash(member), hash('verified'))
        self.assertIn('verified', {member})
        self.assertIn(member, {'verified'})
        self.assertEqual(Counter([member, 'verified'])['verified'], 2)

    def test_json_round_trip_is_plain_strings(self):
        encoded = json.dumps({OwnershipStatus.REJECTED: [OwnershipStatus.NEEDS_REVIEW]})
        self.assertEqual(encoded, '{"rejected": ["needs_review"]}')
        self.assertEqual(OwnershipStatus(json.loads(encoded)['rejected'][0]), OwnershipStatus.NEEDS_REVIEW)

    def test_every_vocabulary_has_unique_snake_case_values(self):
        self.assertGreaterEqual(len(VOCABULARIES), 15)
        for vocabulary in VOCABULARIES:
            values = vocabulary.values()
            self.assertEqual(len(values), len(set(values)), vocabulary.__name__)
            for value in values:
                self.assertRegex(value, r'^[a-z][a-z_]*$', vocabulary.__name__)

    def test_policy_choices_follow_the_enum(self):
        self.assertEqual(AMBIGUITY_POLICIES, ('stop', 'collect_contenders'))
        self.assertIs(AmbiguityPolicy('collect_contenders'), AmbiguityPolicy.COLLECT_CONTENDERS)


if __name__ == '__main__':
    unittest.main()


class ModelValidationTests(unittest.TestCase):
    def test_status_fields_are_coerced_to_their_enum(self):
        from paper_author_searcher.models import PaperDownloadResult

        record = PaperDownloadResult('key', 'Title', 'verified', download_status='failed')
        self.assertIs(record.ownership_status, OwnershipStatus.VERIFIED)
        self.assertEqual(json.dumps(record.download_status), '"failed"')

    def test_unknown_status_is_rejected_with_the_allowed_values(self):
        from paper_author_searcher.models import PDFProviderResult

        with self.assertRaisesRegex(ValueError, "PDFProviderResult.status: 'done' is not one of ok, partial"):
            PDFProviderResult('arxiv', status='done')
