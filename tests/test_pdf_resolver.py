import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import SourceError, URLProbe
from paper_author_searcher.models import PDFCandidate, PDFProviderResult
from paper_author_searcher.pdf_cli import main
from paper_author_searcher.pdf_providers import LandingPagePDFProvider, OpenAlexPDFProvider
from paper_author_searcher.pdf_resolver import PDFResolver
from paper_author_searcher.pdf_resource_requests import PublicResourceRequests
from paper_author_searcher.pdf_validation import PDFURLValidator


def saved_collection():
    return {
        'collection': {
            'papers': [
                {
                    'title': 'First study',
                    'doi': '10.1234/first',
                    'ownership_status': 'verified',
                    'ownership_evidence': ['Original author verification'],
                },
                {'title': 'Second study', 'doi': None, 'ownership_status': 'needs_review'},
                {'title': 'Third study', 'doi': None, 'ownership_status': 'rejected'},
            ]
        }
    }


def provider(name='public_api', url='https://repo.example.edu/paper.pdf', identity='verified', version='unknown'):
    item = PDFCandidate(
        url,
        name,
        access_basis='Explicit OA PDF metadata',
        identity_status=identity,
        version_type=version,
        provenance=[{'provider': name, 'source_paper_id': 'test-paper-id'}],
    )
    result = Mock()
    result.name = name
    result.find.return_value = PDFProviderResult(name, candidates=[item])
    return result


def validator(body=b'%PDF-1.7'):
    client = Mock()
    client.probe.side_effect = lambda url, *args, **kwargs: URLProbe(url, 200, 'application/pdf', {}, body)
    return PDFURLValidator(client)


class ResolverTests(unittest.TestCase):
    def test_only_verified_ownership_processed_by_default(self):
        source = provider()
        saved = saved_collection()
        before = deepcopy(saved)
        result = PDFResolver([source], validator()).resolve(saved)
        self.assertEqual(source.find.call_count, 1)
        self.assertEqual(result.statistics['total_papers'], 3)
        self.assertEqual(result.statistics['valid_pdf_papers'], 1)
        self.assertEqual(result.statistics['skipped_ownership_papers'], 2)
        self.assertEqual(result.papers[0].ownership_evidence, ['Original author verification'])
        self.assertEqual(saved, before)

    def test_explicit_review_opt_in_preserves_status_and_rejected_skip(self):
        source = provider()
        result = PDFResolver([source], validator()).resolve(saved_collection(), include_needs_review=True)
        self.assertEqual(source.find.call_count, 2)
        self.assertEqual(result.papers[1].ownership_status, 'needs_review')
        self.assertEqual(result.papers[1].validation_status, 'verified')
        self.assertEqual(result.papers[2].validation_status, 'skipped_ownership')

    def test_unknown_ownership_conservatively_skipped(self):
        saved = {'papers': [{'title': 'Old archive study'}]}
        result = PDFResolver([], validator()).resolve(saved)
        self.assertEqual(result.papers[0].ownership_status, 'needs_review')
        self.assertEqual(result.papers[0].validation_status, 'skipped_ownership')

    def test_maximum_papers_is_explicit_and_remainder_not_called_missing(self):
        result = PDFResolver([provider()], validator()).resolve(saved_collection(), True, 1)
        self.assertEqual(result.statistics['not_processed_papers'], 1)
        self.assertEqual(result.statistics['not_found_papers'], 0)
        with self.assertRaises(ValueError):
            PDFResolver([], Mock()).resolve(saved_collection(), maximum_papers=0)

    def test_provider_failure_does_not_prevent_other_source_success(self):
        failed = provider('unavailable')
        failed.find.side_effect = SourceError('HTTP 429')
        result = PDFResolver([failed, provider()], validator()).resolve(saved_collection())
        self.assertEqual(result.statistics['valid_pdf_papers'], 1)
        self.assertEqual(result.statistics['incomplete_search_papers'], 1)
        self.assertFalse(result.statistics['search_complete_for_processed_papers'])
        self.assertEqual(result.papers[0].providers[0]['status'], 'error')
        self.assertTrue(result.papers[0].error_details)

    def test_links_without_identity_do_not_count_as_valid(self):
        result = PDFResolver([provider(identity='unverified')], validator()).resolve(saved_collection())
        self.assertEqual(result.statistics['valid_pdf_papers'], 0)
        self.assertEqual(result.statistics['unverified_only_papers'], 1)
        self.assertIsNone(result.papers[0].best_pdf_url)
        self.assertTrue(result.papers[0].pdf_candidates[0].pdf_signature_confirmed)

    def test_non_pdf_candidates_are_retained_but_not_counted_valid(self):
        client = Mock()
        client.probe.return_value = URLProbe(
            'https://repo.example.edu/page', 200, 'text/html', {}, b'<html>blocked</html>'
        )
        result = PDFResolver([provider()], PDFURLValidator(client)).resolve(saved_collection())
        self.assertEqual(result.statistics['not_found_papers'], 1)
        self.assertEqual(len(result.papers[0].pdf_candidates), 1)
        self.assertEqual(result.papers[0].pdf_candidates[0].validation_status, 'invalid')

    def test_urls_are_deduplicated_without_losing_sources(self):
        first, second = provider('openalex'), provider('semantic_scholar')
        check = validator()
        result = PDFResolver([first, second], check).resolve(saved_collection())
        self.assertEqual(check.client.probe.call_count, 1)
        self.assertEqual(len(result.papers[0].pdf_candidates[0].provenance), 2)
        self.assertEqual(result.statistics['source_contributions']['openalex']['verified_urls'], 1)
        self.assertEqual(result.statistics['source_contributions']['semantic_scholar']['verified_urls'], 1)
        self.assertEqual(result.statistics['distinct_verified_pdf_urls'], 1)

    def test_same_url_conflicting_metadata_blocks_automatic_selection(self):
        result = PDFResolver([provider(), provider('second', identity='conflicting')], validator()).resolve(
            saved_collection()
        )
        self.assertEqual(result.statistics['valid_pdf_papers'], 0)
        self.assertEqual(result.papers[0].pdf_candidates[0].identity_status, 'conflicting')
        self.assertIsNone(result.papers[0].best_pdf_url)

    def test_all_versions_preserved_and_published_preferred(self):
        sources = [
            provider('arxiv', 'https://repo.example.edu/preprint', version='preprint'),
            provider('openalex', 'https://repo.example.edu/published', version='published_version'),
        ]
        result = PDFResolver(sources, validator()).resolve(saved_collection())
        self.assertEqual(len(result.papers[0].pdf_candidates), 2)
        self.assertEqual(result.papers[0].version_type, 'published_version')
        self.assertEqual(result.papers[0].best_pdf_url, 'https://repo.example.edu/published')

    def test_fragment_dedup_preserves_query_parameter_variants(self):
        sources = [
            provider('one', 'https://repo.example.edu/paper?version=1'),
            provider('two', 'https://repo.example.edu/paper?version=1#page=2'),
            provider('three', 'https://repo.example.edu/paper?version=2'),
        ]
        result = PDFResolver(sources, validator()).resolve(saved_collection())
        self.assertEqual(len(result.papers[0].pdf_candidates), 2)

    def test_version_conflict_remains_unknown_after_third_source(self):
        sources = [
            provider('one', version='preprint'),
            provider('two', version='published_version'),
            provider('three', version='published_version'),
        ]
        result = PDFResolver(sources, validator()).resolve(saved_collection())
        self.assertEqual(result.papers[0].version_type, 'unknown')
        self.assertEqual(len(result.papers[0].pdf_candidates[0].provenance), 3)

    def test_filtered_collection_uses_all_papers(self):
        saved = saved_collection()['collection']
        saved['all_papers'] = saved['papers']
        saved['papers'] = []
        self.assertEqual(PDFResolver([], validator()).resolve(saved).statistics['total_papers'], 3)

    def test_empty_no_sources_does_not_invent_urls(self):
        result = PDFResolver([], validator()).resolve(saved_collection())
        self.assertEqual(result.statistics['not_found_papers'], 1)
        self.assertEqual(result.papers[0].pdf_candidates, [])
        self.assertEqual(PDFResolver([], Mock()).resolve({'papers': []}).statistics['total_papers'], 0)

    def test_invalid_collection_rejected(self):
        for saved in ({'collection': []}, {}, {'papers': ['not an object']}):
            with self.assertRaises(ValueError):
                PDFResolver([], Mock()).resolve(saved)

    def test_new_api_oa_landing_metadata_available_to_page_provider(self):
        saved = {
            'papers': [
                {
                    'title': 'First study',
                    'doi': '10.1234/first',
                    'ownership_status': 'verified',
                    'records': [{'source': 'openalex', 'source_paper_id': 'W101'}],
                }
            ]
        }
        client = Mock()
        client.get.return_value = {
            'id': 'W101',
            'title': 'First study',
            'doi': '10.1234/first',
            'locations': [{'is_oa': True, 'landing_page_url': 'https://repo.example.edu/page'}],
        }
        html = b'<meta name="citation_doi" content="10.1234/first"><meta name="citation_pdf_url" content="/a.pdf">'

        def probe(url, *args, **kwargs):
            if url.endswith('/page'):
                return URLProbe(url, 200, 'text/html', {}, html)
            return URLProbe(url, 200, 'application/pdf', {}, b'%PDF-1.7')

        client.probe.side_effect = probe
        result = PDFResolver(
            [OpenAlexPDFProvider(client), LandingPagePDFProvider(client)], PDFURLValidator(client)
        ).resolve(saved)
        self.assertEqual(result.statistics['valid_pdf_papers'], 1)
        self.assertEqual(result.papers[0].best_pdf_url, 'https://repo.example.edu/a.pdf')
        self.assertNotIn('_pdf_landing_pages', saved['papers'][0])

    def test_report_serializable_and_validation_scope_explicit(self):
        result = PDFResolver([provider()], validator()).resolve(saved_collection()).to_dict()
        json.dumps(result)
        self.assertIn('not_full_text', result['validation_scope'])
        self.assertEqual(result['papers'][0]['pdf_candidates'][0]['bytes_read'], 8)

    def test_download_metadata_preserved_in_new_url_reports(self):
        saved = {
            'papers': [
                {
                    'title': 'First study',
                    'doi': '10.1234/first',
                    'ownership_status': 'verified',
                    'authors': ['Alex Example'],
                    'year': 2020,
                    'source': 'openalex',
                    'source_paper_id': 'W101',
                    'external_ids': {'doi': '10.1234/first'},
                }
            ]
        }
        result = PDFResolver([], validator()).resolve(saved).to_dict()['papers'][0]
        for name in ('authors', 'year', 'source', 'source_paper_id', 'external_ids'):
            self.assertEqual(result[name], saved['papers'][0][name])


class Clock:
    """A monotonic clock that only the resolver's sleep advances."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class FlakyClient:
    """Fails the first probes of each URL with the given errors, then serves a PDF."""

    def __init__(self, *failures):
        self.failures = list(failures)
        self.urls = []

    def probe(self, url, *args, **kwargs):
        self.urls.append(url)
        if self.failures:
            raise self.failures.pop(0)
        return URLProbe(url, 200, 'application/pdf', {}, b'%PDF-1.7')


class TransientRetryTests(unittest.TestCase):
    def resolve(self, client, sources=None, **changes):
        clock = Clock()
        config = replace(DEFAULT_PDF, public_page_request_interval=0, **changes)
        requests = PublicResourceRequests(client, config)
        resolver = PDFResolver(
            sources or [provider()], PDFURLValidator(requests, config), requests, config, clock.sleep
        )
        with patch('paper_author_searcher.pdf_resource_requests.time.monotonic', clock.monotonic):
            return resolver.resolve(saved_collection()), clock

    def test_a_link_that_timed_out_is_probed_again_after_its_host_cools_down(self):
        client = FlakyClient(SourceError('timed out', error_type='timeout', endpoint_host='repo.example.edu'))
        result, clock = self.resolve(client)
        paper = result.papers[0]
        self.assertEqual(paper.validation_status, 'verified')
        self.assertEqual(len(client.urls), 2)
        self.assertEqual(clock.slept, [DEFAULT_PDF.public_page_cooldown])
        self.assertFalse(any(entry.get('error_type') == 'timeout' for entry in paper.error_details))
        self.assertEqual(paper.pdf_candidates[0].error_details, [])
        self.assertEqual(result.statistics['transient_retry']['recovered_papers'], 1)

    def test_a_definitive_refusal_is_not_retried(self):
        client = FlakyClient(SourceError('HTTP 403', 403, 'access_restricted', endpoint_host='repo.example.edu'))
        result, clock = self.resolve(client)
        self.assertEqual(result.papers[0].validation_status, 'unverified')
        self.assertEqual(len(client.urls), 1)
        self.assertEqual(clock.slept, [])

    def test_a_paper_with_a_verified_link_is_not_retried(self):
        sources = [provider('first', 'https://slow.example.edu/a.pdf'), provider('second')]
        client = FlakyClient(SourceError('HTTP 503', 503, 'server_error', endpoint_host='slow.example.edu'))
        result, _ = self.resolve(client, sources)
        self.assertEqual(result.papers[0].validation_status, 'verified')
        self.assertEqual(len(client.urls), 2)
        self.assertEqual(result.statistics['transient_retry']['retried_links'], 0)

    def test_links_without_identity_are_not_retried(self):
        client = FlakyClient(SourceError('timed out', error_type='timeout', endpoint_host='repo.example.edu'))
        result, _ = self.resolve(client, [provider(identity='unverified')])
        self.assertEqual(len(client.urls), 1)

    def test_the_wait_is_capped_and_retries_can_be_disabled(self):
        limit = SourceError('HTTP 429', 429, 'rate_limited', retry_after=600, endpoint_host='repo.example.edu')
        _, clock = self.resolve(FlakyClient(limit), transient_retry_maximum_wait=30)
        self.assertEqual(clock.slept, [30])
        client = FlakyClient(SourceError('timed out', error_type='timeout', endpoint_host='repo.example.edu'))
        result, clock = self.resolve(client, transient_retry_rounds=0)
        self.assertEqual((len(client.urls), clock.slept), (1, []))
        self.assertEqual(result.papers[0].validation_status, 'unverified')


class SourceRetryTests(unittest.TestCase):
    def failing(self, error_type='rate_limited', status_code=429):
        return PDFProviderResult(
            'public_api',
            status='error',
            errors=['10.1234/first: HTTP 429'],
            diagnostics=[
                {'identifier': '10.1234/first', 'status': 'error', 'status_code': status_code, 'error_type': error_type}
            ],
        )

    def resolve(self, source, client=None, **changes):
        clock = Clock()
        config = replace(DEFAULT_PDF, public_page_request_interval=0, **changes)
        requests = PublicResourceRequests(client or FlakyClient(), config)
        resolver = PDFResolver([source], PDFURLValidator(requests, config), requests, config, clock.sleep)
        with patch('paper_author_searcher.pdf_resource_requests.time.monotonic', clock.monotonic):
            return resolver.resolve(saved_collection()), clock

    def test_a_throttled_source_is_asked_again_and_its_failure_forgotten(self):
        source = provider()
        answer = source.find.return_value
        source.find.side_effect = [self.failing(), answer]
        result, clock = self.resolve(source)
        paper = result.papers[0]
        self.assertEqual(paper.validation_status, 'verified')
        self.assertEqual(clock.slept, [DEFAULT_PDF.source_retry_wait])
        self.assertEqual([entry['status'] for entry in paper.providers], [answer.status])
        self.assertFalse(paper.error_details)
        self.assertEqual(result.statistics['transient_retry']['source_recovered_papers'], 1)

    def test_a_definitive_answer_is_not_asked_again(self):
        source = provider()
        source.find.side_effect = [self.failing('not_found', 404)]
        result, clock = self.resolve(source)
        self.assertEqual(source.find.call_count, 1)
        self.assertEqual(clock.slept, [])
        self.assertEqual(result.statistics['transient_retry']['reasked_sources'], 0)

    def test_a_source_still_failing_stays_reported_and_rounds_are_bounded(self):
        source = provider()
        source.find.side_effect = [self.failing(), self.failing()]
        result, _ = self.resolve(source)
        self.assertEqual(source.find.call_count, 2)
        self.assertEqual(len(result.papers[0].providers), 1)
        self.assertTrue(result.papers[0].error_details)

    def test_retries_can_be_disabled(self):
        source = provider()
        source.find.side_effect = [self.failing()]
        _, clock = self.resolve(source, source_retry_rounds=0)
        self.assertEqual((source.find.call_count, clock.slept), (1, []))

    def test_a_link_from_the_second_answer_can_itself_be_retried(self):
        source = provider()
        answer = source.find.return_value
        source.find.side_effect = [self.failing(), answer]
        client = FlakyClient(SourceError('timed out', error_type='timeout', endpoint_host='repo.example.edu'))
        result, _ = self.resolve(source, client)
        self.assertEqual(result.papers[0].validation_status, 'verified')
        self.assertEqual(len(client.urls), 2)


class CLITests(unittest.TestCase):
    def test_saved_archive_cli_writes_url_report_without_api_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / 'papers.json'
            output_path = Path(directory) / 'pdf_urls.json'
            input_path.write_text(json.dumps(saved_collection()), encoding='utf-8')
            resolver = PDFResolver([provider()], validator())
            with patch('paper_author_searcher.composition.PDFResolver', return_value=resolver), patch('builtins.print'):
                code = main(['--input', str(input_path), '--output', str(output_path), '--max-papers', '1'])
            self.assertEqual(code, 0)
            result = json.loads(output_path.read_text(encoding='utf-8'))
            self.assertEqual(result['statistics']['distinct_verified_pdf_urls'], 1)
            self.assertEqual({path.suffix for path in Path(directory).iterdir()}, {'.json'})
            self.assertEqual(json.loads(input_path.read_text(encoding='utf-8')), saved_collection())

    def test_cli_refuses_to_overwrite_input(self):
        with patch('sys.stderr'), self.assertRaises(SystemExit) as raised:
            main(['--input', 'papers.json', '--output', 'papers.json'])
        self.assertEqual(raised.exception.code, 2)

    def test_cli_partial_provider_failure_still_saves_report(self):
        failed = provider()
        failed.find.side_effect = SourceError('offline')
        resolver = PDFResolver([failed], validator())
        with tempfile.TemporaryDirectory() as directory:
            input_path, output_path = Path(directory) / 'input.json', Path(directory) / 'output.json'
            input_path.write_text(json.dumps(saved_collection()), encoding='utf-8')
            with patch('paper_author_searcher.composition.PDFResolver', return_value=resolver), patch('builtins.print'):
                self.assertEqual(main(['--input', str(input_path), '--output', str(output_path)]), 2)
            self.assertTrue(output_path.exists())
