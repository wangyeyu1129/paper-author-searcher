import unittest
from dataclasses import asdict

from paper_author_searcher.config import CollectionConfig, RawMetadataPolicy
from paper_author_searcher.models import Paper, PaperRecord
from paper_author_searcher.raw_metadata import apply_policy, retained

OPENALEX = {
    'id': 'W1',
    'locations': [{'pdf_url': 'https://x/p.pdf'}],
    'authorships': [{'a': 1}] * 50,
    'abstract_inverted_index': {},
}


def record(source='openalex', raw=None):
    return PaperRecord(
        source=source,
        source_paper_id='W1',
        title='T',
        authors=[],
        year=2020,
        doi=None,
        venue=None,
        abstract=None,
        paper_url=None,
        source_author_id='A1',
        raw_metadata=dict(raw or OPENALEX),
    )


class RawMetadataTests(unittest.TestCase):
    def test_minimal_keeps_only_fields_later_stages_read(self):
        kept = retained('openalex', OPENALEX, RawMetadataPolicy.MINIMAL)
        self.assertEqual(sorted(kept), ['id', 'locations'])
        self.assertEqual(retained('dblp', {'xml': '<r/>'}, RawMetadataPolicy.MINIMAL), {})

    def test_full_and_none(self):
        self.assertEqual(retained('openalex', OPENALEX, RawMetadataPolicy.FULL), OPENALEX)
        self.assertEqual(retained('openalex', OPENALEX, RawMetadataPolicy.NONE), {})

    def test_policy_applies_to_papers_and_their_records(self):
        source_records = [record(), record('semantic_scholar', {'paperId': 'S', 'openAccessPdf': None, 'tldr': 'x'})]
        paper = Paper(**asdict(record()), records=source_records)
        apply_policy([paper], RawMetadataPolicy.MINIMAL)
        self.assertEqual(sorted(paper.raw_metadata), ['id', 'locations'])
        self.assertEqual(sorted(paper.records[1].raw_metadata), ['openAccessPdf', 'paperId'])

    def test_unknown_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            CollectionConfig(raw_metadata='some')


if __name__ == '__main__':
    unittest.main()
