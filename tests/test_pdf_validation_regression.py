import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from paper_author_searcher.config import DEFAULT_DOWNLOAD, DEFAULT_PDF
from paper_author_searcher.http import DownloadReceipt, SourceError, URLProbe
from paper_author_searcher.models import PDFFileValidation
from paper_author_searcher.pdf_downloader import PDFDownloader
from paper_author_searcher.pdf_file_validation import inspect_pdf
from paper_author_searcher.pdf_resource_requests import PublicResourceRequests
from paper_author_searcher.pdf_revalidation import revalidate
from paper_author_searcher.pdf_validation_benchmark import benchmark_inputs, run_benchmark
from paper_author_searcher.performance_diagnostics import workflow_outcomes
from pdf_fixtures import HAS_PYPDF, make_pdf
from test_pdf_downloader import fake_client, target


@unittest.skipUnless(HAS_PYPDF, 'pypdf is required')
class ContentRegressionTests(unittest.TestCase):
    def inspect(self, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'paper.pdf'
            path.write_bytes(make_pdf(**kwargs))
            return inspect_pdf(path, target(), DEFAULT_DOWNLOAD)

    def test_matching_poster_identity_is_not_confirmed_full_paper(self):
        result = self.inspect(metadata_title='poster.ppt', page_size=(2500, 3400))
        self.assertEqual(result.format_status, 'valid')
        self.assertEqual(result.identity_status, 'verified')
        self.assertEqual(result.document_type, 'poster')
        self.assertEqual(result.validation_status, 'needs_review')

    def test_matching_slide_deck_does_not_count_as_full_paper(self):
        result = self.inspect(creator='LaTeX with beamer', pages=3, page_size=(800, 450))
        self.assertEqual(result.document_type, 'slides')
        self.assertEqual(result.validation_status, 'needs_review')

    def test_explicit_publication_year_conflict_retains_review(self):
        paper = target()
        lines = [
            paper['title'],
            '; '.join(paper['authors']),
            'DOI: ' + paper['doi'],
            'Published 2018',
            'Abstract',
            'Research content',
        ]
        result = self.inspect(text_lines=lines)
        self.assertEqual(result.identity_status, 'needs_review')
        self.assertEqual(result.observations['publication_years'], ['2018'])

    def test_body_doi_cannot_verify_missing_front_matter_authors(self):
        paper = target()
        result = self.inspect(
            doi=None,
            authors='Other Author',
            text_lines=[paper['title'], 'Other Author', 'Abstract', 'A body citation: ' + paper['doi']],
        )
        self.assertEqual(result.identity_status, 'needs_review')
        self.assertEqual(result.extracted_dois, [])

    def test_actual_title_and_doi_conflicts_still_rejected(self):
        result = self.inspect(title='Unrelated scientific research report', doi='10.1234/other')
        self.assertEqual(result.identity_status, 'rejected')


class TransportRegressionTests(unittest.TestCase):
    def test_poster_retained_and_next_full_text_candidate_tried(self):
        paper = target()
        paper['pdf_candidates'].append(dict(paper['pdf_candidates'][0], url='https://repo.example.edu/full'))
        client = fake_client()

        def transfer(url, path, config):
            body = ('%PDF-synthetic-fixture-' + url).encode('ascii')
            path.write_bytes(body)
            return DownloadReceipt(url, 200, 'application/pdf', len(body), hashlib.sha256(body).hexdigest())

        client.stream_pdf.side_effect = transfer
        validator = Mock()
        validator.validate.side_effect = [
            PDFFileValidation(
                format_status='valid',
                identity_status='verified',
                validation_status='needs_review',
                document_type='poster',
            ),
            PDFFileValidation(
                format_status='valid',
                identity_status='verified',
                validation_status='verified',
                document_type='full_text',
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = PDFDownloader(client, validator).download({'papers': [paper]}, Path(directory))
            entry = result['papers'][0]
            self.assertEqual(entry['document_type'], 'full_text')
            self.assertEqual(entry['validation_status'], 'verified')
            self.assertEqual([attempt['status'] for attempt in entry['attempts']], ['non_full_document', 'completed'])
            self.assertEqual(result['statistics']['failed_papers'], 0)

    def test_poster_only_is_review_without_runtime_failure(self):
        validator = Mock()
        validator.validate.return_value = PDFFileValidation(
            format_status='valid', identity_status='verified', validation_status='needs_review', document_type='poster'
        )
        with tempfile.TemporaryDirectory() as directory:
            result = PDFDownloader(fake_client(), validator).download({'papers': [target()]}, Path(directory))
            self.assertEqual(result['papers'][0]['download_status'], 'non_full_document')
            self.assertEqual(result['statistics']['verified_papers'], 0)
            self.assertEqual(result['statistics']['needs_review_papers'], 1)
            self.assertEqual(result['statistics']['non_full_document_papers'], 1)
            self.assertEqual(result['statistics']['failed_papers'], 0)

    def test_redirect_limit_cools_actual_host_not_doi_gateway(self):
        client = Mock()
        client.probe.side_effect = [
            SourceError('HTTP 429', 429, endpoint_host='publisher.example.edu'),
            URLProbe('https://other.example.edu/article', 200, 'text/html', {}, b'short'),
        ]
        config = replace(DEFAULT_PDF, public_page_request_interval=0)
        requests = PublicResourceRequests(client, config)
        with self.assertRaises(SourceError):
            requests.probe('https://doi.org/10.1234/one', 16, 64)
        with self.assertRaises(SourceError) as raised:
            requests._before_redirect('https://publisher.example.edu/another')
        self.assertEqual(raised.exception.error_type, 'source_deferred')
        requests.probe('https://doi.org/10.1234/two', 16, 64)
        self.assertEqual(client.probe.call_count, 2)
        self.assertEqual(requests.statistics['rate_limit_responses'], 1)

    def test_offline_revalidation_checks_hash_and_does_not_invent_evidence(self):
        body = b'%PDF-synthetic-fixture'
        entry = dict(
            target(),
            local_filename='paper.pdf',
            file_size=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            format_status='valid',
            validation_status='needs_review',
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'paper.pdf').write_bytes(body)
            (root / 'manifest.json').write_text(json.dumps({'papers': [entry]}))
            (root / 'pdf_urls.json').write_text(json.dumps({'papers': [target()]}))
            validator = Mock()
            validator.validate.return_value = PDFFileValidation(format_status='valid', validation_status='needs_review')
            result = revalidate(root / 'manifest.json', root / 'pdf_urls.json', validator=validator)
            self.assertTrue(result['papers'][0]['file_integrity_confirmed'])
            (root / 'paper.pdf').write_bytes(b'changed')
            result = revalidate(root / 'manifest.json', root / 'pdf_urls.json', validator=validator)
            self.assertFalse(result['papers'][0]['file_integrity_confirmed'])
            self.assertNotIn('after', result['papers'][0])
            self.assertEqual(validator.validate.call_count, 1)

    def test_outcome_categories_keep_filter_decisions_separate(self):
        report = {
            'errors': [{'error': 'HTTP 429'}, {'error': 'timed out'}, {'error': 'Query incomplete'}],
            'warnings': [{'code': 'metadata_preview_limit', 'incomplete': True}],
            'candidate_decisions': [{'category': 'candidate_filtered'}, {'category': 'pdf_identity_conflict'}],
            'download_statistics': {'failed_papers': 1, 'rejected_content_papers': 1},
        }
        result = workflow_outcomes(report)
        self.assertEqual(
            result['error_observation_counts'], {'source_unavailable': 1, 'network_failure': 1, 'query_incomplete': 1}
        )
        self.assertEqual(result['candidate_decision_counts'], {'candidate_filtered': 1, 'pdf_identity_conflict': 1})


class BenchmarkTests(unittest.TestCase):
    def write_inputs(self, root):
        paper = dict(target(), validation_status='verified')
        (root / 'pdf_urls.json').write_text(json.dumps({'papers': [paper]}))
        (root / 'papers.json').write_text(json.dumps({'papers': [paper]}))

    def test_cohort_is_original_saved_metadata_without_ground_truth_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_inputs(root)
            archive, count = benchmark_inputs(root)
            self.assertEqual(count, 1)
            self.assertEqual(archive['papers'][0]['title'], target()['title'])

    def test_changed_original_cohort_fails_before_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_inputs(root)
            (root / 'papers.json').write_text(json.dumps({'papers': [dict(target(), doi='10.1234/other')]}))
            with self.assertRaisesRegex(ValueError, 'ordering'):
                benchmark_inputs(root)

    def test_missing_contact_stops_benchmark_without_requests(self):
        with patch('paper_author_searcher.pdf_validation_benchmark.contact_email', return_value=None), patch(
            'paper_author_searcher.pdf_validation_benchmark.HttpClient'
        ) as client:
            with self.assertRaisesRegex(ValueError, 'UNPAYWALL_EMAIL'):
                run_benchmark(Path('baseline'), Path('output'), 6, 0)
            client.assert_not_called()

    def test_changed_year_hidden_by_same_doi_fails_before_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_inputs(root)
            (root / 'papers.json').write_text(json.dumps({'papers': [dict(target(), year=2021)]}))
            with self.assertRaisesRegex(ValueError, 'metadata differs'):
                benchmark_inputs(root)

    def test_partial_search_still_writes_comparison_without_contact_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = {'papers': [target()]}
            with patch(
                'paper_author_searcher.pdf_validation_benchmark.contact_email', return_value='private@example.test'
            ), patch(
                'paper_author_searcher.pdf_validation_benchmark.benchmark_inputs', return_value=(archive, 1)
            ), patch(
                'paper_author_searcher.pdf_validation_benchmark.summarize_runs',
                return_value={'runs': [{'statistics': {}}]},
            ), patch('paper_author_searcher.pdf_validation_benchmark.PaperSearchWorkflow') as workflow, patch(
                'paper_author_searcher.pdf_validation_benchmark.PDFResolver'
            ), patch('paper_author_searcher.pdf_validation_benchmark.PDFDownloader'), patch(
                'paper_author_searcher.pdf_validation_benchmark.compare_runs', return_value={'before': {}, 'after': {}}
            ):
                workflow.return_value.run.return_value = {'run_status': 'partial', 'output_directory': str(root)}
                _, result = run_benchmark(Path('baseline'), root, 6, 0)
            self.assertNotIn('private@example.test', json.dumps(result))
            self.assertEqual(result['benchmark_configuration']['timeout'], 6)
            self.assertEqual(result['benchmark_configuration']['retries'], 0)
            self.assertIsNone(result['benchmark_configuration']['baseline_http_configuration'])
            self.assertTrue((root / 'comparison.json').exists())
            self.assertEqual(workflow.return_value.run.call_args[1]['saved'], archive)
            self.assertEqual(workflow.return_value.run.call_args[1]['maximum_pdf_papers'], 1)
