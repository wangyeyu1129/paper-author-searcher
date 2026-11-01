"""Discovery regressions use synthetic metadata, never a professor-specific rule."""

import json
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import SourceError, URLProbe
from paper_author_searcher.pdf_cli import main as pdf_main
from paper_author_searcher.pdf_crossref import CrossrefPDFProvider
from paper_author_searcher.pdf_identity import arxiv_ids
from paper_author_searcher.pdf_providers import SemanticScholarPDFProvider, UnpaywallPDFProvider, openalex_landing_pages
from paper_author_searcher.pdf_public_pages import PublicPagePDFProvider, secure_page_url
from paper_author_searcher.pdf_resolver import PDFResolver
from paper_author_searcher.pdf_source_requests import SemanticScholarRequests
from paper_author_searcher.performance_diagnostics import pdf_failure_reasons
from paper_author_searcher.settings import Credentials, validate_contact_email
from paper_author_searcher.workflow_cli import main as workflow_main


def saved_paper(doi='10.1234/method'):
    return {
        'title': 'Evidence for a general method',
        'year': 2020,
        'authors': ['Alex Example'],
        'doi': doi,
        'ownership_status': 'verified',
        'records': [],
    }


def semantic_work(doi='10.1234/method'):
    return {
        'paperId': 'synthetic-id',
        'title': 'Evidence for a general method',
        'year': 2020,
        'authors': [{'name': 'Alex Example'}],
        'externalIds': {'DOI': doi} if doi else {},
        'openAccessPdf': {'url': 'https://repo.example.edu/method.pdf', 'status': 'GREEN'},
    }


def crossref_work():
    return {
        'DOI': '10.1234/method',
        'title': ['Evidence for a general method'],
        'author': [{'given': 'Alex', 'family': 'Example'}],
        'issued': {'date-parts': [[2020]]},
        'link': [
            {
                'URL': 'https://journal.example.org/method.pdf',
                'content-type': 'application/pdf',
                'content-version': 'vor',
            }
        ],
        'URL': 'https://journal.example.org/article',
    }


def probe(url, body, content_type='text/html'):
    return URLProbe(url, 200, content_type, {}, body.encode('utf-8'))


def citation_page():
    return (
        '<meta name="citation_title" content="Evidence for a general method">'
        '<meta name="citation_author" content="Alex Example">'
        '<meta name="citation_date" content="2020">'
    )


class SemanticRequestTests(unittest.TestCase):
    def gateway(self, client):
        return SemanticScholarRequests(client, config=replace(DEFAULT_PDF, semantic_scholar_request_interval=0))

    def test_batch_fields_are_query_parameters_and_cached(self):
        client = Mock()
        client.post_json.return_value = [semantic_work(), None]
        gateway = self.gateway(client)
        gateway.prefetch(['DOI:10.1234/method', 'DOI:10.1234/absent', 'DOI:10.1234/method'])
        self.assertEqual(gateway.work('DOI:10.1234/method')['paperId'], 'synthetic-id')
        with self.assertRaises(SourceError) as raised:
            gateway.work('DOI:10.1234/absent')
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(client.post_json.call_args[0][1], {'ids': ['DOI:10.1234/method', 'DOI:10.1234/absent']})
        self.assertIn('fields', client.post_json.call_args[1]['params'])
        self.assertIs(client.post_json.call_args[1]['expected_type'], list)
        client.get.assert_not_called()

    def test_configured_batch_size_and_missing_records(self):
        client = Mock()
        client.post_json.side_effect = [[None, None], [None]]
        gateway = SemanticScholarRequests(
            client, config=replace(DEFAULT_PDF, semantic_scholar_batch_size=2, semantic_scholar_request_interval=0)
        )
        gateway.prefetch(['one', 'two', 'three'])
        self.assertEqual(client.post_json.call_count, 2)
        self.assertEqual(len(gateway.cache), 3)

    def test_malformed_batch_recovers_through_individual_lookup(self):
        client = Mock()
        client.post_json.return_value = []
        client.get.return_value = semantic_work()
        gateway = self.gateway(client)
        gateway.prefetch(['DOI:10.1234/method'])
        gateway.work('DOI:10.1234/method')
        self.assertIn('batch_error', gateway.diagnostics('DOI:10.1234/method'))
        self.assertEqual(client.get.call_count, 1)

    def test_rate_limit_defers_only_this_source_without_spamming(self):
        client = Mock()
        client.post_json.side_effect = SourceError('HTTP 429', status_code=429, retry_after=120)
        gateway = self.gateway(client)
        with patch('paper_author_searcher.pdf_source_requests.time.monotonic', return_value=10):
            gateway.prefetch(['one', 'two'])
            for value in ('one', 'two'):
                with self.assertRaises(SourceError):
                    gateway.work(value)
            self.assertEqual(gateway.cooldown_until, 130)
            self.assertTrue(gateway.diagnostics('one')['source_cooldown_active'])
        client.get.assert_not_called()

    def test_timeout_cooldown_expires_and_can_recover(self):
        client = Mock()
        client.get.side_effect = [SourceError('timed out', error_type='timeout'), semantic_work()]
        gateway = self.gateway(client)
        with patch('paper_author_searcher.pdf_source_requests.time.monotonic', return_value=10):
            with self.assertRaises(SourceError):
                gateway.work('one')
        with patch('paper_author_searcher.pdf_source_requests.time.monotonic', return_value=100):
            self.assertEqual(gateway.work('one')['paperId'], 'synthetic-id')

    def test_html_interstitial_cools_source_instead_of_repeating_batch_and_get(self):
        client = Mock()
        client.post_json.side_effect = SourceError('HTML interstitial', error_type='invalid_content_type')
        gateway = self.gateway(client)
        gateway.prefetch(['one'])
        with self.assertRaises(SourceError):
            gateway.work('one')
        client.get.assert_not_called()

    def test_authentication_error_is_not_retried_for_every_paper(self):
        client = Mock()
        client.get.side_effect = SourceError('HTTP 401', status_code=401)
        gateway = self.gateway(client)
        for identifier in ('one', 'two'):
            with self.assertRaises(SourceError):
                gateway.work(identifier)
        self.assertEqual(client.get.call_count, 1)

    def test_individual_404_cached_as_unindexed_not_network_failure(self):
        client = Mock()
        client.get.side_effect = SourceError('HTTP 404', status_code=404)
        gateway = self.gateway(client)
        for _ in range(2):
            with self.assertRaises(SourceError) as raised:
                gateway.work('absent')
            self.assertEqual(raised.exception.error_type, 'not_found')
        self.assertEqual(client.get.call_count, 1)

    def test_error_payload_is_not_success_metadata(self):
        client = Mock()
        client.get.return_value = {'error': 'Unavailable'}
        with self.assertRaises(SourceError) as raised:
            self.gateway(client).work('one')
        self.assertEqual(raised.exception.error_type, 'malformed_response')

    def test_title_search_without_doi_requires_existing_identity_rules(self):
        client = Mock()
        client.get.return_value = {'data': [semantic_work(None)]}
        provider = SemanticScholarPDFProvider(client, config=replace(DEFAULT_PDF, semantic_scholar_request_interval=0))
        self.assertEqual(provider.find(saved_paper(None)).candidates[0].identity_status, 'verified')
        client.get.return_value['data'][0]['authors'] = [{'name': 'Unrelated Person'}]
        other = SemanticScholarPDFProvider(client, config=replace(DEFAULT_PDF, semantic_scholar_request_interval=0))
        self.assertEqual(other.find(saved_paper(None)).candidates[0].identity_status, 'unverified')

    def test_doi_404_falls_back_to_metadata_search(self):
        client = Mock()
        client.get.side_effect = [SourceError('HTTP 404', status_code=404), {'data': [semantic_work()]}]
        provider = SemanticScholarPDFProvider(client, config=replace(DEFAULT_PDF, semantic_scholar_request_interval=0))
        result = provider.find(saved_paper())
        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.candidates[0].identity_status, 'verified')

    def test_only_selected_eligible_cohort_is_prefetched(self):
        client = Mock()
        client.post_json.return_value = [semantic_work()]
        selected = saved_paper()
        review = dict(saved_paper('10.1234/review'), ownership_status='needs_review')
        remaining = saved_paper('10.1234/later')
        provider = SemanticScholarPDFProvider(client)
        validator = Mock()
        result = PDFResolver([provider], validator).resolve({'papers': [selected, review, remaining]}, maximum_papers=1)
        self.assertEqual(client.post_json.call_args[0][1]['ids'], ['DOI:10.1234/method'])
        self.assertEqual(result.papers[1].validation_status, 'skipped_ownership')
        self.assertEqual(result.papers[2].validation_status, 'not_processed')

    def test_rate_limited_provider_does_not_block_other_pdf_sources(self):
        client = Mock()
        client.post_json.side_effect = SourceError('HTTP 429', status_code=429)
        semantic = SemanticScholarPDFProvider(client)
        crossref_client = Mock()
        crossref_client.get.return_value = {'status': 'ok', 'message': crossref_work()}
        config = replace(DEFAULT_PDF, source_retry_rounds=0)
        resolver = PDFResolver([semantic, CrossrefPDFProvider(crossref_client)], Mock(), config=config)
        report = resolver.resolve({'papers': [saved_paper()]}).to_dict()
        self.assertEqual(report['papers'][0]['providers'][0]['status'], 'partial')
        self.assertTrue(report['papers'][0]['providers'][0]['warnings'])
        self.assertEqual(report['papers'][0]['providers'][1]['status'], 'ok')
        self.assertTrue(report['papers'][0]['pdf_candidates'])
        self.assertIn('search_incomplete', pdf_failure_reasons(dict(report['papers'][0], pdf_candidates=[])))


class ContactTests(unittest.TestCase):
    def test_required_unpaywall_stops_cli_before_any_provider_requests(self):
        with (
            patch.dict('os.environ', {}, clear=True),
            patch('sys.stderr'),
            patch('paper_author_searcher.composition.PDFResolver') as resolver,
        ):
            with self.assertRaises(SystemExit) as raised:
                pdf_main(['--input', 'missing.json', '--output', 'result.json', '--require-unpaywall'])
        self.assertEqual(raised.exception.code, 2)
        resolver.assert_not_called()

    def test_required_unpaywall_stops_workflow_before_building_stages(self):
        with (
            patch.dict('os.environ', {}, clear=True),
            patch('sys.stderr'),
            patch('paper_author_searcher.workflow_cli.build_workflow') as workflow,
        ):
            with self.assertRaises(SystemExit) as raised:
                workflow_main(['--input', 'missing.json', '--mode', 'full', '--require-unpaywall'])
        self.assertEqual(raised.exception.code, 2)
        workflow.assert_not_called()

    def test_email_read_from_environment_not_persisted_in_result(self):
        client = Mock()
        client.get.return_value = {'doi': '10.1234/method', 'is_oa': False}
        credentials = Credentials.from_environment({'UNPAYWALL_EMAIL': ' owner@example.org '})
        provider = UnpaywallPDFProvider(client, credentials.unpaywall_email)
        result = provider.find(saved_paper())
        self.assertEqual(client.get.call_args[1]['params'], {'email': 'owner@example.org'})
        self.assertNotIn('owner@example.org', str(result))

    def test_invalid_email_is_disabled_without_contact_leak(self):
        value = 'Invalid Contact <private@example.org>'
        provider = UnpaywallPDFProvider(Mock(), value)
        result = provider.find(saved_paper())
        self.assertEqual(result.status, 'disabled')
        self.assertNotIn('private@example.org', str(result))
        provider.client.get.assert_not_called()

    def test_unconfigured_provider_remains_explicitly_disabled(self):
        # Providers never read the environment; credentials are injected by the composition root.
        with patch.dict('os.environ', {'UNPAYWALL_EMAIL': 'owner@example.org'}):
            self.assertEqual(UnpaywallPDFProvider(Mock()).find(saved_paper()).status, 'disabled')

    def test_error_contact_value_is_redacted(self):
        client = Mock()
        client.get.side_effect = SourceError('failure owner@example.org')
        result = UnpaywallPDFProvider(client, 'owner@example.org').find(saved_paper())
        self.assertNotIn('owner@example.org', str(result))

    def test_url_encoded_contact_in_errors_is_redacted(self):
        client = Mock()
        client.get.side_effect = SourceError('failure email=owner%40example.org')
        result = UnpaywallPDFProvider(client, 'owner@example.org').find(saved_paper())
        self.assertNotIn('owner%40example.org', str(result))

    def test_landing_only_oa_record_is_forwarded_not_assumed_pdf(self):
        client = Mock()
        client.get.return_value = {
            'doi': '10.1234/method',
            'is_oa': True,
            'oa_locations': [
                {'url_for_landing_page': 'https://repo.example.edu/article', 'version': 'acceptedVersion'}
            ],
        }
        result = UnpaywallPDFProvider(client, 'owner@example.org').find(saved_paper())
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.landing_pages[0]['landing_page_url'], 'https://repo.example.edu/article')

    def test_email_whitespace_is_stripped(self):
        self.assertEqual(validate_contact_email(' other@example.org '), 'other@example.org')
        self.assertIsNone(validate_contact_email('  '))


class CrossrefTests(unittest.TestCase):
    def provider(self, work):
        client = Mock()
        client.get.return_value = {'status': 'ok', 'message': work}
        return CrossrefPDFProvider(client)

    def test_deposited_pdf_links_and_versions_require_validation(self):
        result = self.provider(crossref_work()).find(saved_paper())
        self.assertEqual(result.candidates[0].identity_status, 'verified')
        self.assertEqual(result.candidates[0].validation_status, 'unverified')
        self.assertEqual(result.candidates[0].version_type, 'published_version')

    def test_html_and_doi_links_are_not_guessed_as_pdf(self):
        work = crossref_work()
        work['link'][0]['content-type'] = 'text/html'
        result = self.provider(work).find(saved_paper())
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.landing_pages[0]['landing_page_url'], work['URL'])

    def test_conflicting_doi_is_not_verified(self):
        work = crossref_work()
        work['DOI'] = '10.1234/other'
        self.assertEqual(self.provider(work).find(saved_paper()).candidates[0].identity_status, 'conflicting')

    def test_missing_doi_title_search_checks_year_and_full_byline(self):
        work = crossref_work()
        work.pop('DOI')
        provider = self.provider({'items': [work]})
        result = provider.find(saved_paper(None))
        self.assertEqual(result.candidates[0].identity_status, 'verified')
        self.assertIn('query.title', provider.client.get.call_args[1]['params'])
        work['author'].append({'given': 'Unrelated', 'family': 'Person'})
        self.assertEqual(
            self.provider({'items': [work]}).find(saved_paper(None)).candidates[0].identity_status, 'unverified'
        )

    def test_non_crossref_doi_404_can_use_bounded_metadata_search(self):
        client = Mock()
        client.get.side_effect = [
            SourceError('HTTP 404', status_code=404),
            {'status': 'ok', 'message': {'items': [crossref_work()]}},
        ]
        result = CrossrefPDFProvider(client).find(saved_paper())
        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.candidates[0].identity_status, 'verified')

    def test_malformed_author_metadata_is_source_error(self):
        work = crossref_work()
        work['author'] = 'invalid'
        self.assertEqual(self.provider(work).find(saved_paper()).status, 'error')


class PublicPageTests(unittest.TestCase):
    def discover(self, html):
        client = Mock()
        client.probe.return_value = probe('https://repo.example.edu/article', html)
        provider = PublicPagePDFProvider(client)
        saved = saved_paper(None)
        saved['paper_url'] = 'https://repo.example.edu/article'
        return provider.find(saved), client

    def test_alternate_pdf_with_citation_metadata(self):
        result, _ = self.discover(citation_page() + '<link rel="alternate" type="application/pdf" href="paper.pdf">')
        self.assertEqual(result.candidates[0].url, 'https://repo.example.edu/paper.pdf')
        self.assertEqual(result.candidates[0].identity_status, 'verified')

    def test_arbitrary_pdf_anchor_does_not_establish_identity(self):
        result, _ = self.discover('<a href="unrelated.pdf">PDF</a>')
        self.assertEqual(result.candidates, [])

    def test_repository_dublin_core_and_explicit_document_metadata(self):
        html = (
            '<meta name="DC.title" content="Evidence for a general method">'
            '<meta name="DC.creator" content="Alex Example"><meta name="DC.date" content="2020">'
            '<meta name="eprints.document_url" content="paper.pdf">'
        )
        result, _ = self.discover(html)
        self.assertEqual(result.candidates[0].identity_status, 'verified')

    def test_supplied_http_path_uses_tls_without_guessing_a_pdf(self):
        self.assertEqual(
            secure_page_url('http://repo.example.edu/author/article?id=1'),
            'https://repo.example.edu/author/article?id=1',
        )
        with self.assertRaises(SourceError):
            secure_page_url('http://127.0.0.1/private')

    def test_institutional_pdf_with_stale_oa_flag_requires_observed_pdf(self):
        saved = saved_paper()
        saved['_pdf_landing_pages'] = [
            {
                'landing_page_url': 'http://repo.example.edu/paper.pdf',
                'database': 'openalex',
                'doi': saved['doi'],
                'title': saved['title'],
                'is_oa': False,
            }
        ]
        client = Mock()
        client.probe.return_value = probe('https://repo.example.edu/paper.pdf', '%PDF-1.7', 'application/pdf')
        result = PublicPagePDFProvider(client).find(saved)
        self.assertEqual(result.candidates[0].identity_status, 'verified')
        self.assertIsNone(result.candidates[0].license)
        self.assertEqual(client.probe.call_args[0][0], 'https://repo.example.edu/paper.pdf')
        client.probe.return_value = probe('https://repo.example.edu/paper.pdf', '<html>blocked</html>')
        self.assertEqual(PublicPagePDFProvider(client).find(saved).candidates, [])

    def test_unmarked_unknown_host_cannot_be_trusted_from_oa_binding(self):
        saved = saved_paper()
        saved['_pdf_landing_pages'] = [
            {
                'landing_page_url': 'https://unknown.example.org/file',
                'database': 'openalex',
                'doi': saved['doi'],
                'title': saved['title'],
                'is_oa': False,
            }
        ]
        client = Mock()
        client.probe.return_value = probe('https://unknown.example.org/file', '%PDF-1.7', 'application/pdf')
        self.assertEqual(PublicPagePDFProvider(client).find(saved).candidates, [])

    def test_bibliography_title_alone_stays_unverified(self):
        client = Mock()
        root = 'https://faculty.example.edu/publications'
        client.probe.side_effect = [
            probe(root, '<a href="method.pdf">Evidence for a general method</a>'),
            probe('https://faculty.example.edu/method.pdf', '%PDF-1.7', 'application/pdf'),
        ]
        provider = PublicPagePDFProvider(client, replace(DEFAULT_PDF, bibliography_urls=(root,)))
        result = provider.find(saved_paper())
        self.assertEqual(result.candidates[0].identity_status, 'unverified')

    def test_bibliography_article_page_can_supply_independent_metadata(self):
        client = Mock()
        root = 'https://faculty.example.edu/publications'
        child = citation_page() + '<meta name="citation_pdf_url" content="method.pdf">'
        client.probe.side_effect = [
            probe(root, '<a href="method">Evidence for a general method</a>'),
            probe('https://faculty.example.edu/method', child),
        ]
        provider = PublicPagePDFProvider(client, replace(DEFAULT_PDF, bibliography_urls=(root,)))
        self.assertEqual(provider.find(saved_paper(None)).candidates[0].identity_status, 'verified')

    def test_jsonld_metadata_carries_authors_and_requires_matching_byline(self):
        item = {
            '@type': 'ScholarlyArticle',
            'name': 'Evidence for a general method',
            'datePublished': '2020',
            'author': [{'name': 'Alex Example'}],
            'encoding': {'encodingFormat': 'application/pdf', 'contentUrl': 'method.pdf'},
        }
        result, _ = self.discover('<script type="application/ld+json">' + json.dumps(item) + '</script>')
        self.assertEqual(result.candidates[0].identity_status, 'verified')
        item['author'] = [{'name': 'Other Person'}]
        result, _ = self.discover('<script type="application/ld+json">' + json.dumps(item) + '</script>')
        self.assertEqual(result.candidates[0].identity_status, 'unverified')

    def test_shared_bibliography_is_cached_across_papers(self):
        client = Mock()
        root = 'https://faculty.example.edu/publications'
        client.probe.return_value = probe(root, '<html>No matching records</html>')
        provider = PublicPagePDFProvider(client, replace(DEFAULT_PDF, bibliography_urls=(root,)))
        provider.find(saved_paper())
        provider.find(dict(saved_paper(), title='Another method'))
        self.assertEqual(client.probe.call_count, 1)

    def test_private_link_does_not_prevent_other_candidates(self):
        html = (
            citation_page() + '<link rel="alternate" type="application/pdf" href="http://127.0.0.1/file">'
            '<link rel="alternate" type="application/pdf" href="method.pdf">'
        )
        result, _ = self.discover(html)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.warnings[0]['code'], 'candidate_rejected')


class IdentifierDiagnosticTests(unittest.TestCase):
    def test_supplied_non_oa_pdf_url_can_be_probed_without_claiming_open_access(self):
        location = {'pdf_url': 'https://repo.example.edu/method.pdf', 'is_oa': False}
        pages = openalex_landing_pages({'doi': '10.1234/method', 'locations': [location]})
        self.assertEqual(pages[0]['landing_page_url'], location['pdf_url'])
        self.assertIs(pages[0]['is_oa'], False)
        self.assertEqual(pages[0]['supplied_location'], location)

    def test_arxiv_ids_from_supplied_locations_and_namespace_doi(self):
        saved = saved_paper('10.48550/arXiv.2401.00001')
        saved['raw_metadata'] = {'locations': [None, {'landing_page_url': 'https://arxiv.org/abs/2401.00001v2'}]}
        self.assertEqual(arxiv_ids(saved), ['2401.00001v2', '2401.00001'])

    def test_metadata_404_is_not_pdf_url_404(self):
        saved = {
            'validation_status': 'not_found',
            'providers': [{'status': 'not_found', 'diagnostics': [{'error_type': 'not_found', 'status_code': 404}]}],
            'error_details': [{'error': 'HTTP 404 from API'}],
        }
        reasons = pdf_failure_reasons(saved)
        self.assertIn('metadata_record_not_found', reasons)
        self.assertNotIn('url_not_found', reasons)
