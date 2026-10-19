"""End-to-end contracts using synthetic providers and the existing stage algorithms."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from paper_author_searcher.cli import main as legacy_main
from paper_author_searcher.config import StorageConfig
from paper_author_searcher.http import SourceError
from paper_author_searcher.jsonio import read_json, write_json
from paper_author_searcher.models import AuthorCandidate, PDFCandidate, PDFProviderResult, ProfessorQuery
from paper_author_searcher.paper_collector import PaperCollector
from paper_author_searcher.paper_views import full_paper_metadata
from paper_author_searcher.pdf_downloader import PDFDownloader
from paper_author_searcher.pdf_file_validation import PDFFileValidator
from paper_author_searcher.pdf_resolver import PDFResolver
from paper_author_searcher.resolver import ProfessorResolver
from paper_author_searcher.workflow import PaperSearchWorkflow, WorkflowStage, load_checkpoint, metadata_audit
from paper_author_searcher.workflow_cli import main
from pdf_fixtures import HAS_PYPDF, make_pdf
from test_paper_collector import paper
from test_pdf_downloader import fake_client, fake_validator
from test_pdf_resolver import validator

QUERY = ProfessorQuery('Alex Example', 'Example University')


def author_provider(source='openalex', author_id='A1', candidates=None):
    provider = Mock(name=source)
    provider.name = source
    provider.search.return_value = candidates or [
        AuthorCandidate(source, author_id, QUERY.name, [QUERY.university], orcid='0000-0000-0000-0001')
    ]
    return provider


def record(
    source='openalex',
    author_id='A1',
    paper_id='W1',
    title='Reusable methods for synthetic research',
    doi='10.1234/example',
):
    item = paper(source, paper_id, doi, title, 2020)
    item.source_author_id = author_id
    item.authors = [QUERY.name, 'Taylor Researcher']
    item.publication_type = 'journal-article'
    item.author_details = [
        {
            'author_id': author_id,
            'name': QUERY.name,
            'role': 'author',
            'orcid': '0000-0000-0000-0001',
            'institutions': [{'display_name': QUERY.university}],
        }
    ]
    item.target_author_present = True
    item.authors_complete = True
    item.external_ids = {'DOI': doi} if doi else {}
    item.raw_metadata = {'fixture_evidence': {'paper_id': paper_id}}
    return item


def paper_provider(source='openalex', records=None, error=None, complete=True):
    from paper_author_searcher.models import PaperSourceResult

    provider = Mock()
    provider.name = source
    if isinstance(error, Exception):
        provider.collect.side_effect = error
    else:
        items = records if records is not None else [record()]
        provider.collect.side_effect = lambda author_id: PaperSourceResult(
            source,
            author_id,
            deepcopy(items),
            complete=complete,
            pagination_complete=complete,
            records_received=len(items),
            pages_fetched=1,
            error=error,
        )
    return provider


def pdf_provider():
    provider = Mock()
    provider.name = 'public_repository'
    provider.find.side_effect = lambda item: PDFProviderResult(
        provider.name,
        [
            PDFCandidate(
                'https://repo.example.edu/' + item['source_paper_id'] + '.pdf',
                provider.name,
                identity_status='verified',
                access_basis='Explicit public API metadata',
                provenance=[{'provider': provider.name, 'source_paper_id': item['source_paper_id']}],
            )
        ],
    )
    return provider


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.author = author_provider()
        self.papers = paper_provider()
        self.urls = pdf_provider()
        self.client = fake_client()
        self.progress = Mock()
        self.workflow = PaperSearchWorkflow(
            ProfessorResolver([self.author]),
            PaperCollector([self.papers]),
            PDFResolver([self.urls], validator()),
            PDFDownloader(self.client, fake_validator()),
            self.progress,
        )

    def run_workflow(self, **kwargs):
        kwargs.setdefault('query', QUERY)
        kwargs.setdefault('output_dir', self.root)
        return self.workflow.run(**kwargs)

    def output(self, report, artifact):
        return read_json(Path(report['output_directory']) / report['artifacts'][artifact])

    def test_full_chain_preserves_metadata_provenance_and_folder_layout(self):
        report = self.run_workflow()
        self.assertEqual(report['run_status'], 'completed')
        archive = self.output(report, 'papers')['collection']['papers'][0]
        resolved = self.output(report, 'pdf_urls')['papers'][0]
        manifest = self.output(report, 'manifest')
        downloaded = manifest['papers'][0]
        for field in (
            'title',
            'authors',
            'year',
            'doi',
            'source',
            'source_paper_id',
            'source_author_id',
            'ownership_status',
            'ownership_evidence',
            'external_ids',
        ):
            self.assertEqual(archive[field], resolved[field])
            self.assertEqual(archive[field], downloaded[field])
        # papers.json is the one full store; downstream artifacts keep identity fields and a reference to it.
        for entry in (resolved, downloaded):
            view = entry['paper_metadata']
            self.assertEqual(view['source_identifiers'], archive['source_identifiers'])
            self.assertEqual([record['source_paper_id'] for record in view['records']],
                             [record['source_paper_id'] for record in archive['records']])  # fmt: skip
            self.assertNotIn('raw_metadata', json.dumps(view))
            self.assertNotIn('author_details', view)
            self.assertEqual(full_paper_metadata(entry, [archive]), archive)
        self.assertEqual(manifest['file_root'], 'papers')
        self.assertTrue(downloaded['local_filename'].startswith('2020_'))
        self.assertTrue((Path(report['output_directory']) / 'papers' / downloaded['local_filename']).exists())
        self.assertFalse(report['metadata_audit']['transmission_losses'])
        stages = [call.args[0] for call in self.progress.call_args_list]
        for stage in (
            'Resolving professor identity',
            'Collecting papers',
            'Deduplicating papers',
            'Validating ownership',
            'Finding PDF URLs',
            'Downloading PDFs',
            'Generating report',
        ):
            self.assertIn(stage, stages)

    def test_modes_do_not_call_later_stages(self):
        report = self.run_workflow(mode='metadata')
        self.urls.find.assert_not_called()
        self.client.stream_pdf.assert_not_called()
        self.assertNotIn('pdf_urls', report['artifacts'])
        report = self.run_workflow(mode='pdf-urls')
        self.urls.find.assert_called_once()
        self.client.stream_pdf.assert_not_called()
        self.assertNotIn('manifest', report['artifacts'])

    def test_cross_source_deduplication_retains_both_author_ids(self):
        second_author = author_provider('semantic_scholar', 'S1')
        self.workflow.resolver = ProfessorResolver([self.author, second_author])
        self.workflow.collector = PaperCollector(
            [self.papers, paper_provider('semantic_scholar', [record('semantic_scholar', 'S1', 'P1')])]
        )
        report = self.run_workflow()
        papers = self.output(report, 'papers')['collection']['papers']
        self.assertEqual(len(papers), 1)
        self.assertEqual(len(papers[0]['records']), 2)
        self.assertEqual({item['author_id'] for item in papers[0]['source_identifiers']}, {'A1', 'S1'})
        self.assertEqual(report['collection_statistics']['raw_records_received'], 2)
        self.client.stream_pdf.assert_called_once()

    def test_ambiguous_identity_stops_and_displays_candidates(self):
        candidates = [AuthorCandidate('openalex', item, QUERY.name, [QUERY.university]) for item in ('A1', 'A2')]
        self.workflow.resolver = ProfessorResolver([author_provider(candidates=candidates)])
        report = self.run_workflow()
        self.assertEqual(report['run_status'], 'identity_confirmation_required')
        self.assertEqual(len(report['identity']['candidates']), 2)
        self.papers.collect.assert_not_called()
        self.urls.find.assert_not_called()
        checkpoint = load_checkpoint(Path(report['output_directory']) / 'report.json')
        report = self.workflow.run(
            saved=checkpoint, selected_author='openalex:A2', output_dir=self.root, mode='metadata'
        )
        self.assertNotEqual(report['run_status'], 'identity_confirmation_required')
        self.papers.collect.assert_called_once_with('A2')
        selected = self.output(report, 'papers')['collection']['selected_author_ids']
        self.assertEqual(selected, {'openalex': ['A2']})

    def test_contender_policy_collects_competing_profiles_without_verifying(self):
        from dataclasses import replace

        from paper_author_searcher.config import DEFAULT_COLLECTION

        candidates = [AuthorCandidate('openalex', item, QUERY.name, [QUERY.university]) for item in ('A1', 'A2')]
        self.workflow.resolver = ProfessorResolver([author_provider(candidates=candidates)])
        self.workflow.collection_config = replace(DEFAULT_COLLECTION, ambiguity_policy='collect_contenders')
        report = self.run_workflow(mode='metadata')
        self.assertNotEqual(report['run_status'], 'identity_confirmation_required')
        self.assertEqual(sorted(call.args[0] for call in self.papers.collect.call_args_list), ['A1', 'A2'])
        collection = self.output(report, 'papers')['collection']
        self.assertEqual(collection['identity_confirmation'], 'ambiguous_contenders')
        statuses = {item['ownership_status'] for paper in collection['papers'] for item in [paper] + paper['records']}
        self.assertNotIn('verified', statuses)
        self.assertTrue(any('competing profiles' in warning.get('warning', '') for warning in report['warnings']))

    def test_invalid_selection_stops_before_collection(self):
        report = self.run_workflow(selected_author='openalex:unknown')
        self.assertEqual(report['run_status'], 'identity_confirmation_required')
        self.papers.collect.assert_not_called()

    def test_resolver_provider_failure_preserved_but_other_provider_continues(self):
        failed = author_provider('unavailable')
        failed.search.side_effect = SourceError('HTTP 429')
        self.workflow.resolver = ProfessorResolver([failed, self.author])
        report = self.run_workflow()
        self.assertEqual(report['download_statistics']['verified_papers'], 1)
        self.assertEqual(report['run_status'], 'partial')
        self.assertTrue(any(item.get('source') == 'unavailable' for item in report['errors']))

    def test_collection_failure_and_partial_pagination_do_not_stop_available_papers(self):
        self.workflow.collector = PaperCollector(
            [
                paper_provider(error='Configured page limit', complete=False),
                paper_provider('unavailable', error=SourceError('HTTP 503')),
            ]
        )
        report = self.run_workflow()
        self.assertEqual(report['download_statistics']['verified_papers'], 1)
        self.assertFalse(report['collection_complete'])
        self.assertTrue(any(item['error'] == 'Configured page limit' for item in report['errors']))

    def test_pdf_provider_failure_is_independent(self):
        failed = pdf_provider()
        failed.name = 'failed_pdf_source'
        failed.find.side_effect = RuntimeError('Unavailable provider')
        self.workflow.pdf_resolver = PDFResolver([failed, self.urls], validator())
        report = self.run_workflow()
        self.assertEqual(report['download_statistics']['verified_papers'], 1)
        self.assertEqual(report['run_status'], 'partial')

    def test_one_download_failure_does_not_stop_next_paper(self):
        self.workflow.collector = PaperCollector(
            [
                paper_provider(
                    records=[
                        record(),
                        record(paper_id='W2', title='Independent synthetic research study', doi='10.1234/other'),
                    ]
                )
            ]
        )
        success = self.client.stream_pdf.side_effect
        calls = iter([False, True])
        self.client.stream_pdf.side_effect = lambda *args: success(*args) if next(calls) else self.fail_transfer()
        report = self.run_workflow()
        self.assertEqual(report['download_statistics']['failed_papers'], 1)
        self.assertEqual(report['download_statistics']['verified_papers'], 1)
        self.assertEqual(report['run_status'], 'partial')
        self.assertTrue(any('403' in item['error'] for item in report['errors']))

    @staticmethod
    def fail_transfer():
        raise SourceError('HTTP 403')

    def test_resume_report_reuses_files_and_never_requeries_apis(self):
        first = self.run_workflow()
        directory = Path(first['output_directory'])
        saved = load_checkpoint(directory / 'report.json')
        self.author.search.reset_mock()
        self.papers.collect.reset_mock()
        self.urls.find.reset_mock()
        self.client.stream_pdf.reset_mock()
        report = self.workflow.run(saved=saved, resume_dir=directory)
        self.assertEqual(report['download_statistics']['reused_existing_papers'], 1)
        for operation in (self.author.search, self.papers.collect, self.urls.find, self.client.stream_pdf):
            operation.assert_not_called()
        self.assertEqual(self.output(report, 'papers'), self.output(first, 'papers'))

    def test_resume_collection_starts_at_pdf_stage(self):
        first = self.run_workflow(mode='metadata')
        saved = load_checkpoint(Path(first['output_directory']) / 'report.json')
        before = deepcopy(saved)
        self.author.search.reset_mock()
        self.papers.collect.reset_mock()
        report = self.workflow.run(saved=saved, output_dir=self.root)
        self.author.search.assert_not_called()
        self.papers.collect.assert_not_called()
        self.assertEqual(report['download_statistics']['verified_papers'], 1)
        self.assertEqual(saved, before)

    def test_resume_identity_skips_identity_requests(self):
        result = self.workflow.resolver.resolve(QUERY).to_dict()
        self.author.search.reset_mock()
        report = self.workflow.run(saved=result, output_dir=self.root, mode='metadata')
        self.author.search.assert_not_called()
        self.assertEqual(report['collection_statistics']['deduplicated_entries'], 1)

    def test_old_pdf_report_missing_fields_remains_missing(self):
        report = self.run_workflow(mode='pdf-urls')
        saved = self.output(report, 'pdf_urls')
        for field in ('year', 'authors', 'paper_metadata'):
            del saved['papers'][0][field]
        report = self.workflow.run(saved=saved, output_dir=self.root)
        self.assertEqual(report['metadata_audit']['missing_input_fields']['year'], 1)
        entry = self.output(report, 'manifest')['papers'][0]
        self.assertIsNone(entry['year'])
        self.assertEqual(entry['authors'], [])
        self.assertTrue(entry['local_filename'].startswith('unknown_year_'))

    def test_ownership_opt_in_does_not_change_review_or_rejected_labels(self):
        review = record(paper_id='review', doi='10.1234/review')
        review.author_details = []
        review.target_author_present = None
        rejected = record(paper_id='rejected', doi='10.1234/rejected')
        rejected.target_author_present = False
        rejected.author_details[0]['author_id'] = 'wrong-author'
        self.workflow.collector = PaperCollector([paper_provider(records=[review, rejected])])
        report = self.run_workflow()
        self.client.stream_pdf.assert_not_called()
        report = self.run_workflow(include_needs_review=True)
        entries = self.output(report, 'manifest')['papers']
        self.assertEqual(entries[0]['ownership_status'], 'needs_review')
        self.assertEqual(entries[0]['validation_status'], 'verified')
        self.assertEqual(entries[1]['ownership_status'], 'rejected')
        self.assertEqual(entries[1]['download_status'], 'skipped_ownership')

    def test_limits_mark_unprocessed_entries_and_skipped_downloads(self):
        self.workflow.collector = PaperCollector(
            [
                paper_provider(
                    records=[
                        record(),
                        record(paper_id='W2', title='Independent synthetic research study', doi='10.1234/other'),
                    ]
                )
            ]
        )
        report = self.run_workflow(maximum_pdf_papers=1, maximum_downloads=1)
        self.assertEqual(report['pdf_statistics']['not_processed_papers'], 1)
        self.assertEqual(report['download_statistics']['attempted_papers'], 1)
        self.assertEqual(len(self.output(report, 'papers')['collection']['papers']), 2)
        report = self.run_workflow(maximum_downloads=1)
        self.assertEqual(self.output(report, 'manifest')['papers'][1]['download_status'], 'skipped_limit')

    def test_unique_safe_output_folders(self):
        first = self.run_workflow(mode='metadata')
        second = self.run_workflow(mode='metadata')
        self.assertNotEqual(first['output_directory'], second['output_directory'])
        unsafe = ProfessorQuery('../../CON: unsafe/name', QUERY.university)
        report = self.run_workflow(query=unsafe, mode='metadata')
        self.assertEqual(Path(report['output_directory']).parent, self.root.resolve())
        self.assertNotIn(':', Path(report['output_directory']).name)

    def test_metadata_audit_distinguishes_missing_from_lost(self):
        original = [{'title': 'Example', 'year': 2020, 'authors': ['Example']}, {'title': 'Missing'}]
        audit = metadata_audit(original, {'papers': [{'title': 'Example'}, {'title': 'Missing'}]})
        self.assertEqual(audit['missing_input_fields']['year'], 1)
        self.assertEqual({entry['field'] for entry in audit['transmission_losses']}, {'year', 'authors'})

    def test_report_artifact_traversal_and_symlink_refused(self):
        report = self.run_workflow(mode='metadata')
        path = Path(report['output_directory']) / 'report.json'
        data = read_json(path)
        data['artifacts']['papers'] = '../outside.json'
        write_json(path, data)
        with self.assertRaises(ValueError):
            load_checkpoint(path)

    def test_fatal_stage_error_still_writes_report_and_prior_checkpoint(self):
        self.workflow.pdf_resolver.resolve = Mock(side_effect=RuntimeError('Unexpected stage failure'))
        report = self.run_workflow()
        self.assertEqual(report['run_status'], 'failed')
        self.assertIn('papers', report['artifacts'])
        self.assertEqual(read_json(Path(report['output_directory']) / 'report.json'), report)

    @unittest.skipUnless(HAS_PYPDF, 'Install the download extra for real PDF parser integration')
    def test_full_chain_with_actual_pdf_parser_and_front_matter_identity(self):
        self.workflow.downloader = PDFDownloader(fake_client(make_pdf()), PDFFileValidator())
        report = self.run_workflow()
        entry = self.output(report, 'manifest')['papers'][0]
        self.assertEqual(entry['format_status'], 'valid')
        self.assertEqual(entry['identity_status'], 'verified')
        self.assertIn('10.1234/example', entry['validation']['extracted_dois'])
        self.assertEqual(entry['validation']['page_count'], 1)

    def test_cli_and_existing_cli_use_same_workflow(self):
        for entrypoint in (main, legacy_main):
            with patch('paper_author_searcher.workflow_cli.build_workflow', return_value=self.workflow):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = entrypoint(
                        [
                            '--name',
                            QUERY.name,
                            '--university',
                            QUERY.university,
                            '--mode',
                            'full',
                            '--output-dir',
                            str(self.root),
                        ]
                    )
                self.assertEqual(code, 0)
                self.assertIn('Downloads: new=1', buffer.getvalue())
                self.assertNotIn('"papers"', buffer.getvalue())

    def test_summary_resolver_checkpoint_is_accepted_without_new_identity_requests(self):
        result = self.workflow.resolver.resolve(QUERY).to_dict()['profile']
        result['confidence'] = result.pop('identity_confidence')
        result['matched_affiliations'] = result.pop('affiliations')
        self.author.search.reset_mock()
        report = self.workflow.run(saved=result, output_dir=self.root, mode='metadata')
        self.author.search.assert_not_called()
        self.assertEqual(report['collection_statistics']['deduplicated_entries'], 1)

    def test_report_artifact_symlink_refused(self):
        report = self.run_workflow(mode='metadata')
        root = Path(report['output_directory'])
        artifact = root / 'papers.json'
        target = self.root / 'outside.json'
        artifact.replace(target)
        artifact.symlink_to(target)
        with self.assertRaises(ValueError):
            load_checkpoint(root / 'report.json')

    def test_content_review_makes_execution_partial_without_promoting_ownership(self):
        self.workflow.downloader = PDFDownloader(self.client, fake_validator('needs_review'))
        report = self.run_workflow()
        self.assertEqual(report['run_status'], 'partial')
        entry = self.output(report, 'manifest')['papers'][0]
        self.assertEqual(entry['ownership_status'], 'verified')
        self.assertEqual(entry['identity_status'], 'needs_review')
        self.assertTrue(entry['local_filename'].startswith('needs_review/'))

    def test_pdf_validation_failure_is_isolated_to_one_paper(self):
        first, second = record(), record(paper_id='W2', title='Another synthetic study', doi='10.1234/other')
        self.workflow.collector = PaperCollector([paper_provider(records=[first, second])])
        check = validator()
        actual = check.validate
        outcomes = iter([False, True])

        def validate_candidate(candidate, paper):
            if next(outcomes):
                return actual(candidate, paper)
            raise RuntimeError('Validation service error')

        check.validate = validate_candidate
        self.workflow.pdf_resolver = PDFResolver([self.urls], check)
        report = self.run_workflow()
        self.assertEqual(report['pdf_statistics']['valid_pdf_papers'], 1)
        self.assertEqual(report['download_statistics']['verified_papers'], 1)
        self.assertTrue(any(item.get('source') == 'pdf_validation' for item in report['errors']))

    def test_legacy_thin_identity_is_restored_only_from_existing_collection_evidence(self):
        first = self.run_workflow(mode='pdf-urls')
        saved = self.output(first, 'pdf_urls')
        saved['collection_archive'] = self.output(first, 'papers')
        saved['identity_archive'] = {
            'name': QUERY.name,
            'author_ids': {'openalex': ['A1']},
            'confirmation': 'resolver_resolved',
        }
        report = self.workflow.run(saved=saved, output_dir=self.root)
        restored = self.output(report, 'professor')
        self.assertEqual(restored['profile']['status'], 'resolved')
        self.assertEqual(restored['profile']['author_ids'], {'openalex': 'A1'})
        self.assertEqual(restored['profile']['affiliations'], [QUERY.university])

    def test_interruption_preserves_checkpoint_and_resume_completes(self):
        self.client.stream_pdf.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_workflow()
        directory = next(self.root.glob('search_*'))
        report = read_json(directory / 'report.json')
        self.assertEqual(report['run_status'], 'interrupted')
        self.assertIn('pdf_urls', report['artifacts'])
        manifest = read_json(directory / 'manifest.json')
        self.assertFalse(manifest['run_complete'])
        self.assertEqual(manifest['papers'][0]['download_status'], 'interrupted')
        self.workflow.downloader = PDFDownloader(fake_client(), fake_validator())
        resumed = self.workflow.run(saved=load_checkpoint(directory / 'report.json'), resume_dir=directory)
        self.assertEqual(resumed['download_statistics']['verified_papers'], 1)

    def test_cli_rejects_conflicting_input_and_invalid_limits(self):
        for args in (
            ['--input', 'saved.json', '--name', QUERY.name],
            ['--name', QUERY.name, '--university', QUERY.university, '--max-downloads', '0'],
            ['--input', 'saved.json', '--resume-dir', 'other'],
            ['--name', QUERY.name, '--university', QUERY.university, '--timeout', 'nan'],
        ):
            with redirect_stdout(io.StringIO()), patch('sys.stderr', new=io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(args)


if __name__ == '__main__':
    unittest.main()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_injected_stages_run_in_their_modes_and_can_halt(self):
        calls = []

        class Record(WorkflowStage):
            name = 'Recording'
            modes = frozenset({'metadata'})

            def run(self, context):
                calls.append(context.request.mode)
                context.archive = {'papers': []}

        class Halt(WorkflowStage):
            name = 'Halting'

            def run(self, context):
                context.halted = True

        class Never(WorkflowStage):
            name = 'Never'

            def run(self, context):
                calls.append('never')

        workflow = PaperSearchWorkflow(stages=[Record(), Halt(), Never()])
        report = workflow.run(query=QUERY, output_dir=self.root, mode='metadata')
        self.assertEqual(calls, ['metadata'])
        self.assertEqual(report['run_status'], 'running')  # halted before the closing classification
        workflow.run(query=QUERY, output_dir=self.root, mode='full')
        self.assertEqual(calls, ['metadata'])  # Record is metadata-only

    def test_output_directory_name_keeps_word_characters(self):
        root = PaperSearchWorkflow(stages=[])._directory(self.root, 'Zoë Smith-Jones/PhD', None)
        self.assertTrue(root.name.startswith('search_Zoë_Smith-Jones_PhD_'), root.name)


class ArtifactFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_metadata(self, storage):
        workflow = PaperSearchWorkflow(
            ProfessorResolver([author_provider()]), PaperCollector([paper_provider()]), storage=storage
        )
        report = workflow.run(query=QUERY, output_dir=self.root, mode='metadata')
        directory = Path(report['output_directory'])
        return (directory / 'papers.json').read_text(), (directory / 'report.json').read_text()

    def test_machine_artifacts_are_compact_and_the_report_stays_readable(self):
        papers, report = self.run_metadata(StorageConfig())
        self.assertNotIn('\n  ', papers)
        self.assertIn('\n  "', report)
        self.assertEqual(json.loads(papers)['collection']['paper_count'], 1)

    def test_compaction_can_be_turned_off(self):
        papers, _ = self.run_metadata(StorageConfig(compact_artifacts=False))
        self.assertIn('\n  "', papers)
