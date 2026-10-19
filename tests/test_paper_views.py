import unittest
from dataclasses import replace
from unittest.mock import Mock

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.paper_views import (
    MAX_HISTORY,
    bibliographic_view,
    extend_history,
    full_paper_metadata,
)
from paper_author_searcher.pdf_resolver import PDFResolver


def collected_paper():
    return {
        'title': 'A study',
        'year': 2021,
        'authors': ['Mei Lin'],
        'doi': '10.1234/study',
        'ownership_status': 'verified',
        'abstract': 'long abstract',
        'author_details': [{'name': 'Mei Lin', 'author_id': 'A1'}],
        'records': [
            {
                'source': 'openalex',
                'source_paper_id': 'W1',
                'title': 'A study',
                'year': 2021,
                'authors': ['Mei Lin'],
                'raw_metadata': {'huge': 'x' * 1000},
                'author_details': [{'name': 'Mei Lin'}],
            }
        ],
    }


class ViewTests(unittest.TestCase):
    def test_view_keeps_identity_and_drops_payloads(self):
        view = bibliographic_view(collected_paper())
        self.assertEqual((view['title'], view['year'], view['doi']), ('A study', 2021, '10.1234/study'))
        self.assertEqual(view['records'], [{'source': 'openalex', 'source_paper_id': 'W1', 'title': 'A study',
                                            'year': 2021, 'authors': ['Mei Lin']}])  # fmt: skip
        self.assertNotIn('abstract', view)
        self.assertNotIn('author_details', view)

    def test_history_drops_paper_copies_and_is_bounded(self):
        record = {'download_status': 'failed', 'paper_metadata': {'big': 1}, 'title': 'A study', 'history': []}
        for _ in range(MAX_HISTORY + 5):
            record = dict(record, history=extend_history(record))
        self.assertEqual(len(record['history']), MAX_HISTORY)
        self.assertEqual(record['history'][-1], {'download_status': 'failed'})

    def test_join_returns_the_full_record_only_when_it_still_matches(self):
        papers = [collected_paper()]
        entry = {'entry_index': 0, 'title': 'A study', 'doi': '10.1234/study'}
        self.assertEqual(full_paper_metadata(entry, papers)['abstract'], 'long abstract')
        self.assertIsNone(full_paper_metadata(dict(entry, title='Another'), papers))
        self.assertIsNone(full_paper_metadata(dict(entry, entry_index=3), papers))


class ResolverMetadataTests(unittest.TestCase):
    def resolve(self, embed):
        resolver = PDFResolver([], Mock(), config=replace(DEFAULT_PDF, embed_full_metadata=embed))
        return resolver.resolve({'papers': [collected_paper()]}).papers[0].paper_metadata

    def test_reports_carry_the_view_by_default_and_full_record_on_request(self):
        self.assertNotIn('raw_metadata', self.resolve(embed=False)['records'][0])
        self.assertIn('raw_metadata', self.resolve(embed=True)['records'][0])


if __name__ == '__main__':
    unittest.main()
