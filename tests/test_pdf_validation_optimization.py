import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import SourceError, URLProbe
from paper_author_searcher.pdf_document_types import classify_document
from paper_author_searcher.pdf_file_validation import content_identity
from paper_author_searcher.pdf_front_matter import authors_present, metadata_title_kind, title_in_region
from paper_author_searcher.pdf_resource_requests import PublicResourceRequests
from paper_author_searcher.pdf_source_requests import SemanticScholarRequests


def target():
    return {'title': 'Reusable methods for synthetic research', 'doi': None,
            'authors': ['Alex Example', 'Taylor Researcher'], 'year': 2020}


class FrontMatterTests(unittest.TestCase):
    def test_dvi_metadata_is_uninformative_not_a_conflicting_scientific_title(self):
        paper = target()
        front = paper['title'] + '\nAlex Example1 Taylor Researcher2\nAbstract'
        result = content_identity(paper, ['manuscript.dvi'], [front], front, '')
        self.assertEqual(result[0], 'verified')
        self.assertTrue(any('Technical filename' in value for value in result[2]))

    def test_substantive_title_conflict_is_still_not_verified(self):
        paper = target()
        front = paper['title'] + '\nAlex Example\nTaylor Researcher'
        self.assertEqual(content_identity(paper, ['Unrelated scientific findings'], [front], front, '')[0], 'needs_review')

    def test_short_title_threshold_unchanged_despite_cleaner_metadata(self):
        paper = dict(target(), title='Synthetic research')
        front = paper['title'] + '\nAlex Example\nTaylor Researcher'
        self.assertEqual(content_identity(paper, ['manuscript.dvi'], [front], front, '')[0], 'needs_review')

    def test_author_names_in_abstract_or_references_are_not_byline_evidence(self):
        front = target()['title'] + '\nOther Author\nAbstract\nAlex Example and Taylor Researcher'
        self.assertFalse(authors_present(target()['authors'], [front], ''))

    def test_family_name_first_byline_is_supported(self):
        self.assertTrue(authors_present(['Example, Alex', 'Researcher, Taylor'],
                                       ['Alex Example1, Taylor Researcher2\nAbstract'], ''))

    def test_all_authors_required_not_a_single_overlap(self):
        self.assertFalse(authors_present(target()['authors'], ['Alex Example and Other Author'], ''))

    def test_explicit_conflicting_middle_initial_is_not_ignored(self):
        self.assertFalse(authors_present(['Alex M. Example'], ['Alex R. Example'], ''))

    def test_duplicate_expected_author_cannot_reuse_one_byline(self):
        self.assertFalse(authors_present(['Alex Example', 'Alex Example'], ['Alex Example'], ''))

    def test_bibliographic_prefix_not_arbitrary_title_substring(self):
        self.assertEqual(metadata_title_kind('SERIES 123 - ' + target()['title'], target()['title']), 'decorated')
        self.assertEqual(metadata_title_kind('Review of ' + target()['title'], target()['title']), 'conflicting')

    def test_word_spacing_repair_requires_exact_character_sequence(self):
        self.assertTrue(title_in_region(target()['title'], 'Reusable methods for syn thetic research\nAlex Example'))
        self.assertFalse(title_in_region(target()['title'], 'Reusable methods for unrelated research'))

    def test_review_title_containing_target_title_is_not_a_title_match(self):
        self.assertFalse(title_in_region(target()['title'], 'Review of ' + target()['title']))


class DocumentTypeTests(unittest.TestCase):
    def classify(self, metadata, text='Title\nAbstract\nResearch contents', pages=4, size=(612, 792), version=None):
        return classify_document(metadata, text, pages, size, version)[0]

    def test_poster_requires_evidence_not_just_one_page(self):
        self.assertEqual(self.classify({'/Title': 'conference_poster.ppt'}, pages=1, size=(2500, 3400)), 'poster')
        self.assertEqual(self.classify({}, pages=1), 'unknown')

    def test_slides_can_be_same_paper_and_still_not_full_text(self):
        self.assertEqual(self.classify({'/Creator': 'LaTeX with beamer'}, size=(800, 450)), 'slides')

    def test_supplementary_material_heading(self):
        self.assertEqual(self.classify({}, text='Supplementary Material\nSome research'), 'supplementary_material')

    def test_body_reference_to_supplement_does_not_reclassify_article(self):
        self.assertEqual(self.classify({}, text='A research title\nAbstract\nSee supplementary material for details'), 'full_text')

    def test_preprint_version_separate_from_document_form(self):
        self.assertEqual(self.classify({}, version='preprint'), 'preprint')
        self.assertEqual(self.classify({}, version='published_version'), 'full_text')

    def test_unknown_type_not_guessed_from_venue_or_page_count(self):
        self.assertEqual(self.classify({}, text='Unlabelled document', pages=15), 'unknown')

    def test_abstract_alone_cannot_prove_oversized_page_or_landscape_deck_is_full_text(self):
        self.assertEqual(self.classify({}, pages=1, size=(2500, 3400)), 'unknown')
        self.assertEqual(self.classify({}, pages=10, size=(800, 450)), 'unknown')


class PublicRequestTests(unittest.TestCase):
    def requests(self, client, **kwargs):
        config = replace(DEFAULT_PDF, public_page_request_interval=0, html_metadata_bytes=64,
                         preview_bytes=16, public_page_maximum_bytes=512, **kwargs)
        return PublicResourceRequests(client, config)

    def test_truncated_page_expands_once_and_caches_complete_response(self):
        client = Mock()
        url = 'https://repo.example.edu/article'
        client.probe.side_effect = [URLProbe(url, 200, 'text/html', {}, b'x' * 64),
                                    URLProbe(url, 200, 'text/html', {}, b'x' * 90)]
        requests = self.requests(client)
        first = requests.probe(url, 16, 64)
        self.assertTrue(first.body_complete)
        self.assertEqual(requests.probe(url, 16, 64).prefix, first.prefix)
        self.assertEqual(client.probe.call_count, 2)
        self.assertEqual(client.probe.call_args[0][2], 512)

    def test_small_complete_metadata_page_does_not_expand(self):
        client = Mock()
        url = 'https://repo.example.edu/article'
        client.probe.return_value = URLProbe(url, 200, 'text/html', {}, b'short')
        self.assertTrue(self.requests(client).probe(url, 16, 64).body_complete)
        self.assertEqual(client.probe.call_count, 1)

    def test_catalog_with_larger_budget_is_not_refetched_at_smaller_page_limit(self):
        client = Mock()
        url = 'https://journal.example.edu/catalog'
        client.probe.return_value = URLProbe(url, 200, 'text/html', {}, b'x' * 1024)
        response = self.requests(client).probe(url, 16, 1024)
        self.assertEqual(len(response.prefix), 1024)
        self.assertFalse(response.body_complete)
        self.assertEqual(client.probe.call_count, 1)

    def test_pdf_signature_probe_never_expands_to_page_limit(self):
        client = Mock()
        url = 'https://repo.example.edu/paper.pdf'
        client.probe.return_value = URLProbe(url, 200, 'application/pdf', {}, b'%PDF-' + b'x' * 11)
        requests = self.requests(client)
        requests.probe(url, 16, 64)
        self.assertEqual(len(requests.probe(url, 5).prefix), 5)
        self.assertEqual(client.probe.call_count, 1)

    def test_redirect_alias_shares_response_cache(self):
        client = Mock()
        first, final = 'https://doi.org/example', 'https://repo.example.edu/article'
        client.probe.return_value = URLProbe(final, 200, 'text/html', {}, b'short')
        requests = self.requests(client)
        requests.probe(first, 16, 64)
        requests.probe(final, 16, 64)
        self.assertEqual(client.probe.call_count, 1)

    def test_host_429_blocks_other_urls_on_same_host_not_other_hosts(self):
        client = Mock()
        client.probe.side_effect = [SourceError('HTTP 429', 429, retry_after=600),
                                    URLProbe('https://other.example.edu/article', 200, 'text/html', {}, b'short')]
        requests = self.requests(client)
        with patch('paper_author_searcher.pdf_resource_requests.time.monotonic', return_value=10):
            with self.assertRaises(SourceError):
                requests.probe('https://repo.example.edu/one', 16, 64)
            with self.assertRaises(SourceError) as raised:
                requests.probe('https://repo.example.edu/two', 16, 64)
            self.assertEqual(raised.exception.error_type, 'source_deferred')
            requests.probe('https://other.example.edu/article', 16, 64)
        self.assertEqual(client.probe.call_count, 2)
        self.assertEqual(requests.statistics['rate_limit_responses'], 1)
        self.assertEqual(requests.statistics['cooldown_skips'], 1)

    def test_transient_errors_expire_without_becoming_permanent_absence(self):
        client = Mock()
        url = 'https://repo.example.edu/article'
        client.probe.side_effect = [SourceError('timeout', error_type='timeout'),
                                    URLProbe(url, 200, 'text/html', {}, b'short')]
        requests = self.requests(client)
        with patch('paper_author_searcher.pdf_resource_requests.time.monotonic', return_value=0):
            with self.assertRaises(SourceError):
                requests.probe(url, 16, 64)
        with patch('paper_author_searcher.pdf_resource_requests.time.monotonic', return_value=1000):
            self.assertTrue(requests.probe(url, 16, 64).body_complete)

    def test_404_cached_per_url_not_entire_host(self):
        client = Mock()
        url = 'https://repo.example.edu/absent'
        client.probe.side_effect = [SourceError('HTTP 404', 404),
                                    URLProbe('https://repo.example.edu/found', 200, 'text/html', {}, b'short')]
        requests = self.requests(client)
        for _ in range(2):
            with self.assertRaises(SourceError):
                requests.probe(url, 16, 64)
        requests.probe('https://repo.example.edu/found', 16, 64)
        self.assertEqual(client.probe.call_count, 2)

    def test_semantic_scholar_repeated_limits_increase_cooldown(self):
        client = Mock()
        client.get.side_effect = SourceError('HTTP 429', 429)
        requests = SemanticScholarRequests(client, config=replace(DEFAULT_PDF, semantic_scholar_request_interval=0))
        with patch('paper_author_searcher.pdf_source_requests.time.monotonic', return_value=0):
            with self.assertRaises(SourceError):
                requests.work('one')
            self.assertEqual(requests.cooldown_until, 30)
        with patch('paper_author_searcher.pdf_source_requests.time.monotonic', return_value=31):
            with self.assertRaises(SourceError):
                requests.work('two')
            self.assertEqual(requests.cooldown_until, 91)
