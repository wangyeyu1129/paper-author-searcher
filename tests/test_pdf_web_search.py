import unittest
from dataclasses import replace
from unittest.mock import Mock

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import SourceError
from paper_author_searcher.matching import normalize, normalize_title
from paper_author_searcher.models import PDFCandidate, PDFProviderResult
from paper_author_searcher.pdf_providers import HomepagePDFProvider, WebSearchPDFProvider
from paper_author_searcher.pdf_resolver import PDFResolver
from paper_author_searcher.status import IdentityStatus, ProviderStatus
from paper_author_searcher.web_search import SearchHit

PAPER = {'title': 'A study of many things', 'authors': ['Ada Lovelace', 'Alan Turing'], 'ownership_status': 'verified'}


class FakeSearch:
    name = 'fake'

    def __init__(self, hits=(), error=None):
        self.hits, self.error, self.queries = list(hits), error, []

    def search(self, query, count):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.hits


def pages_finding_nothing():
    pages = Mock()
    pages.find.return_value = PDFProviderResult('public_pages')
    return pages


class TitleSpellingTests(unittest.TestCase):
    def test_british_and_american_titles_compare_equal(self):
        for british, american in (
            ('Neighbourhood income and well-being', 'Neighborhood Income and Wellbeing'),
            ('Modelling organisational behaviour', 'Modeling Organizational Behavior'),
            ('Analysing labour in the centre', 'Analyzing labor in the center'),
        ):
            self.assertEqual(normalize_title(british), normalize_title(american))

    def test_other_words_and_names_stay_distinct(self):
        self.assertNotEqual(normalize_title('Colonial labor'), normalize_title('Colonial harbor'))
        self.assertEqual(normalize('Neighbour'), 'neighbour')

    def test_a_homepage_citation_in_the_other_spelling_serves_the_paper(self):
        provider = HomepagePDFProvider()
        provider.prepare_profile(
            {
                'listed_pdfs': [
                    {'url': 'https://a.example.edu/n.pdf', 'citation': 'Effects of Neighborhood Income, 2020'}
                ]
            }
        )
        paper = {'title': 'Effects of neighbourhood income', 'doi': None}
        provider.prepare([paper])
        self.assertEqual([item.url for item in provider.find(paper).candidates], ['https://a.example.edu/n.pdf'])


class WebSearchProviderTests(unittest.TestCase):
    def test_a_pdf_hit_is_an_unverified_candidate_and_the_query_names_title_and_author(self):
        search = FakeSearch([SearchHit('https://people.example.edu/~ada/study.pdf', 'study.pdf')])
        found = WebSearchPDFProvider(search, pages_finding_nothing()).find(PAPER)
        self.assertEqual(search.queries, ['A study of many things Ada Lovelace pdf'])
        self.assertEqual([item.url for item in found.candidates], ['https://people.example.edu/~ada/study.pdf'])
        self.assertEqual(found.candidates[0].identity_status, IdentityStatus.UNVERIFIED)

    def test_shadow_libraries_and_third_party_uploads_are_never_candidates(self):
        hits = [
            SearchHit('https://sci-hub.se/10.1/x.pdf'),
            SearchHit('https://libgen.example/x.pdf'),
            SearchHit('https://www.scribd.com/x.pdf'),
            SearchHit('https://someone.wordpress.com/x.pdf'),
            SearchHit('https://raw.githubusercontent.com/a/b/x.pdf'),
        ]
        found = WebSearchPDFProvider(FakeSearch(hits), pages_finding_nothing()).find(PAPER)
        self.assertEqual(found.candidates, [])

    def test_only_a_page_naming_the_paper_is_read_up_to_the_limit(self):
        hits = [
            SearchHit('https://a.example.org/1', 'A Study of Many Things'),
            SearchHit('https://a.example.org/2', 'Something else entirely'),
            SearchHit('https://a.example.org/3', '', 'cites: a study of many things.'),
            SearchHit('https://a.example.org/4', 'A study of many things'),
        ]
        pages = pages_finding_nothing()
        WebSearchPDFProvider(FakeSearch(hits), pages).find(PAPER)
        lookup = pages.find.call_args.args[0]
        self.assertEqual(
            [item['landing_page_url'] for item in lookup['_pdf_landing_pages']],
            ['https://a.example.org/1', 'https://a.example.org/3'],
        )

    def test_an_exhausted_key_stops_the_rest_of_the_run_but_a_passing_failure_does_not(self):
        search = FakeSearch(error=SourceError('HTTP 500', status_code=500))
        provider = WebSearchPDFProvider(search, pages_finding_nothing())
        self.assertEqual(provider.find(PAPER).status, ProviderStatus.ERROR)
        self.assertEqual(provider.find(PAPER).status, ProviderStatus.ERROR)
        search.error = SourceError('HTTP 432', status_code=432)
        self.assertEqual(provider.find(PAPER).status, ProviderStatus.ERROR)
        self.assertEqual(provider.find(PAPER).status, ProviderStatus.NOT_APPLICABLE)
        self.assertEqual(len(search.queries), 3)


class FallbackStageTests(unittest.TestCase):
    def resolve(self, papers, primary_candidates, maximum=DEFAULT_PDF.web_search_maximum_papers):
        primary = Mock()
        primary.name = 'primary'
        primary.find.side_effect = lambda paper: PDFProviderResult(
            'primary',
            candidates=[
                PDFCandidate(url, 'primary', access_basis='x', identity_status='verified')
                for url in primary_candidates.get(paper['title'], [])
            ],
        )
        fallback = Mock()
        fallback.name = 'web_search'
        fallback.find.return_value = PDFProviderResult('web_search')
        validator = Mock()

        def validate(candidate, paper):
            candidate.validation_status = 'verified'
            return candidate

        validator.validate.side_effect = validate
        config = replace(DEFAULT_PDF, web_search_maximum_papers=maximum, lookup_workers=1)
        report = PDFResolver([primary], validator, config=config, fallbacks=[fallback]).resolve({'papers': papers})
        return fallback, report

    def test_only_verified_works_without_a_pdf_are_searched_newest_first_and_once(self):
        papers = [
            {'title': 'Has a PDF', 'year': 2024, 'ownership_status': 'verified'},
            {'title': 'Old', 'year': 2001, 'ownership_status': 'verified'},
            {'title': 'New', 'year': 2023, 'ownership_status': 'verified'},
            {'title': 'NEW', 'year': 2022, 'ownership_status': 'verified'},
            {'title': 'Maybe mine', 'year': 2025, 'ownership_status': 'needs_review'},
        ]
        fallback, _ = self.resolve(papers, {'Has a PDF': ['https://x.example.org/a.pdf']})
        self.assertEqual([call.args[0]['title'] for call in fallback.find.call_args_list], ['New', 'Old'])

    def test_an_edition_with_a_pdf_covers_the_work(self):
        papers = [
            {'title': 'Same work', 'year': 2024, 'ownership_status': 'verified'},
            {'title': 'Same work', 'year': 2023, 'ownership_status': 'verified'},
        ]
        fallback, _ = self.resolve([papers[0], papers[1]], {})
        self.assertEqual(fallback.find.call_count, 1)

    def test_the_run_limit_applies(self):
        papers = [
            {'title': f'Work {index}', 'year': 2000 + index, 'ownership_status': 'verified'} for index in range(5)
        ]
        fallback, _ = self.resolve(papers, {}, maximum=2)
        self.assertEqual([call.args[0]['title'] for call in fallback.find.call_args_list], ['Work 4', 'Work 3'])

    def test_a_found_candidate_reaches_every_edition(self):
        papers = [
            {'title': 'Same work', 'year': 2024, 'ownership_status': 'verified'},
            {'title': 'Same work', 'year': 2023, 'ownership_status': 'verified'},
        ]
        primary = Mock()
        primary.name = 'primary'
        primary.find.return_value = PDFProviderResult('primary')
        fallback = Mock()
        fallback.name = 'web_search'
        fallback.find.return_value = PDFProviderResult(
            'web_search',
            candidates=[PDFCandidate('https://x.example.org/f.pdf', 'web_search', access_basis='x')],
        )
        validator = Mock()
        validator.validate.side_effect = lambda candidate, paper: candidate
        report = PDFResolver([primary], validator, fallbacks=[fallback]).resolve({'papers': papers})
        self.assertEqual([len(item.pdf_candidates) for item in report.papers], [1, 1])


if __name__ == '__main__':
    unittest.main()
