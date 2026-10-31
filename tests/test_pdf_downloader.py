import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from paper_author_searcher.config import DEFAULT_DOWNLOAD
from paper_author_searcher.download_cli import main
from paper_author_searcher.http import DownloadReceipt, SourceError
from paper_author_searcher.models import PDFFileValidation
from paper_author_searcher.pdf_downloader import PDFDownloader, paper_filename, paper_key
from paper_author_searcher.pdf_file_validation import PDFFileValidator
from pdf_fixtures import HAS_PYPDF, make_pdf


def target():
    return {
        'title': 'Reusable methods for synthetic research',
        'authors': ['Alex Example', 'Taylor Researcher'],
        'year': 2020,
        'doi': '10.1234/example',
        'ownership_status': 'verified',
        'source': 'openalex',
        'best_pdf_url': 'https://repo.example.edu/document',
        'pdf_candidates': [
            {
                'url': 'https://repo.example.edu/document',
                'source': 'public_api',
                'validation_status': 'verified',
                'identity_status': 'verified',
                'version_type': 'published_version',
            }
        ],
    }


def fake_validator(status='verified', format_status='valid'):
    validator = Mock()
    validator.validate.side_effect = lambda path, paper: PDFFileValidation(
        format_status=format_status,
        identity_status=status,
        validation_status=status,
        page_count=1,
        evidence=['Synthetic validation result for storage/transport tests'],
    )
    return validator


def fake_client(body=b'%PDF-synthetic-fixture'):
    client = Mock()

    def transfer(url, path, config):
        path.write_bytes(body)
        return DownloadReceipt(url, 200, 'application/pdf', len(body), hashlib.sha256(body).hexdigest())

    client.stream_pdf.side_effect = transfer
    return client


def two_copies():
    """A PMC rendering the validator cannot confirm, then an arXiv copy it can."""
    paper = target()
    paper['best_pdf_url'] = 'https://europepmc.org/render'
    paper['pdf_candidates'] = [
        dict(paper['pdf_candidates'][0], url='https://europepmc.org/render', source='europepmc'),
        dict(paper['pdf_candidates'][0], url='https://arxiv.org/pdf/1', source='arxiv'),
    ]
    return paper


def per_url_client():
    client = Mock()

    def transfer(url, path, config):
        body = b'%PDF-' + url.encode()
        path.write_bytes(body)
        return DownloadReceipt(url, 200, 'application/pdf', len(body), hashlib.sha256(body).hexdigest())

    client.stream_pdf.side_effect = transfer
    return client


def content_validator(confirmed=b'arxiv'):
    def validate(path, paper):
        status = 'verified' if confirmed in Path(path).read_bytes() else 'needs_review'
        return PDFFileValidation(format_status='valid', identity_status=status, validation_status=status, page_count=1)

    validator = Mock()
    validator.validate.side_effect = validate
    return validator


class FilenameTests(unittest.TestCase):
    def test_portable_filename_removes_invalid_characters(self):
        paper = dict(target(), title='../CON: A/B\\C*D?E|F"G<>\x00\u202e')
        name = paper_filename(paper)
        self.assertTrue(name.startswith('2020_'))
        self.assertTrue(name.endswith('.pdf'))
        self.assertFalse(any(character in name for character in '<>:"/\\|?*\x00\u202e'))

    def test_long_unicode_title_truncated_without_breaking_utf8(self):
        paper = dict(target(), title='科学研究' * 1000)
        name = paper_filename(paper)
        self.assertLessEqual(len(name.encode('utf-8')), DEFAULT_DOWNLOAD.filename_bytes)
        self.assertIn(paper_key(paper)[:16], name)

    def test_same_title_different_identifier_does_not_collide(self):
        self.assertNotEqual(paper_filename(target()), paper_filename(dict(target(), doi='10.1234/other')))

    def test_identical_paper_keys_ignore_doi_url_spelling(self):
        self.assertEqual(paper_key(target()), paper_key(dict(target(), doi='https://doi.org/10.1234/EXAMPLE')))

    def test_legacy_missing_year_is_not_invented(self):
        self.assertTrue(paper_filename({'title': 'A paper'}).startswith('unknown_year_'))

    def test_source_paper_id_url_and_short_id_share_key(self):
        first = dict(target(), doi=None, source_paper_id='https://openalex.org/W101')
        second = dict(first, source_paper_id='W101')
        self.assertEqual(paper_key(first), paper_key(second))

    def test_configuration_invalid_limits_rejected(self):
        for overrides in (
            {'maximum_file_bytes': 0},
            {'chunk_bytes': 0},
            {'parser_timeout': 0},
            {'allowed_ports': ()},
            {'filename_bytes': 10},
        ):
            with self.assertRaises(ValueError):
                replace(DEFAULT_DOWNLOAD, **overrides)


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.client = fake_client()
        self.validator = fake_validator()
        self.downloader = PDFDownloader(self.client, self.validator)

    def run_download(self, papers=None, **kwargs):
        return self.downloader.download({'papers': papers or [target()]}, self.root, **kwargs)

    def unbound_paper(self):
        paper = target()
        paper['best_pdf_url'] = None
        paper['pdf_candidates'] = [
            {
                'url': 'https://www.example.org/copy.pdf',
                'source': 'public_pages',
                'pdf_signature_confirmed': True,
                'access_status': 'publicly_accessible',
                'validation_status': 'unverified',
                'identity_status': 'unverified',
            }
        ]
        return paper

    def test_a_public_pdf_no_metadata_binds_is_kept_when_its_content_confirms_the_paper(self):
        entry = self.run_download([self.unbound_paper()])['papers'][0]
        self.assertEqual(entry['download_status'], 'downloaded')
        self.assertEqual(entry['identity_status'], 'verified')
        self.assertEqual(entry['pdf_url'], 'https://www.example.org/copy.pdf')
        self.assertTrue((self.root / entry['local_filename']).exists())

    def test_a_public_pdf_no_metadata_binds_leaves_no_file_when_its_content_does_not_confirm(self):
        for status in ('needs_review', 'rejected'):
            self.downloader = PDFDownloader(self.client, fake_validator(status))
            entry = self.run_download([self.unbound_paper()])['papers'][0]
            self.assertEqual(entry['download_status'], 'skipped_unverified_url')
            self.assertIsNone(entry['local_filename'])
            self.assertIsNone(entry['pdf_url'])
            self.assertEqual(entry['attempts'][0]['status'], 'unconfirmed_content')
            self.assertEqual(list(self.root.rglob('*.pdf')), [])

    def test_conflicting_or_unreachable_candidates_are_never_tried_by_content(self):
        for change in (
            {'identity_status': 'conflicting'},
            {'pdf_signature_confirmed': False},
            {'access_status': 'unreachable'},
        ):
            paper = self.unbound_paper()
            paper['pdf_candidates'][0].update(change)
            entry = self.run_download([paper])['papers'][0]
            self.assertEqual(entry['download_status'], 'skipped_unverified_url')
        self.client.stream_pdf.assert_not_called()

    def test_a_bound_url_is_tried_before_an_unbound_one(self):
        paper = target()
        paper['pdf_candidates'].insert(0, self.unbound_paper()['pdf_candidates'][0])
        entry = self.run_download([paper])['papers'][0]
        self.assertEqual(entry['pdf_url'], 'https://repo.example.edu/document')

    def test_the_number_of_content_checks_per_paper_is_limited(self):
        paper = self.unbound_paper()
        template = paper['pdf_candidates'][0]
        paper['pdf_candidates'] = [dict(template, url=f'https://www.example.org/{n}.pdf') for n in range(9)]
        self.downloader = PDFDownloader(self.client, fake_validator('needs_review'))
        self.run_download([paper])
        self.assertEqual(self.client.stream_pdf.call_count, DEFAULT_DOWNLOAD.maximum_content_checks)

    def test_verified_download_manifest_and_original_input_preserved(self):
        saved = {'papers': [target()]}
        before = deepcopy(saved)
        manifest = self.downloader.download(saved, self.root)
        entry = manifest['papers'][0]
        self.assertEqual(entry['download_status'], 'downloaded')
        self.assertEqual(entry['format_status'], 'valid')
        self.assertEqual(entry['identity_status'], 'verified')
        self.assertTrue((self.root / entry['local_filename']).exists())
        self.assertTrue(manifest['run_complete'])
        self.assertEqual(json.loads((self.root / 'manifest.json').read_text()), manifest)
        self.assertEqual(saved, before)
        for name in (
            'title',
            'authors',
            'year',
            'doi',
            'source',
            'pdf_url',
            'local_filename',
            'download_status',
            'validation_status',
            'ownership_status',
            'file_size',
            'sha256',
            'error_details',
        ):
            self.assertIn(name, entry)
        self.assertEqual(list(self.root.glob('*.part')), [])

    def test_review_ownership_requires_opt_in_and_rejected_always_skipped(self):
        papers = [dict(target(), ownership_status='needs_review'), dict(target(), ownership_status='rejected')]
        manifest = self.run_download(papers)
        self.assertEqual(manifest['statistics']['attempted_papers'], 0)
        self.client.stream_pdf.assert_not_called()
        manifest = self.run_download(papers, include_needs_review=True)
        self.assertEqual(manifest['statistics']['attempted_papers'], 1)
        self.assertEqual(manifest['papers'][0]['ownership_status'], 'needs_review')
        self.assertEqual(manifest['papers'][1]['download_status'], 'skipped_ownership')

    def test_unverified_or_conflicting_url_never_downloaded(self):
        for status, identity in (('unverified', 'verified'), ('verified', 'conflicting'), ('invalid', 'verified')):
            paper = target()
            paper['pdf_candidates'][0].update(validation_status=status, identity_status=identity)
            manifest = self.run_download([paper], include_needs_review=True)
            self.assertEqual(manifest['papers'][0]['download_status'], 'skipped_unverified_url')
        self.client.stream_pdf.assert_not_called()

    def test_only_top_level_best_url_not_assumed_verified(self):
        paper = target()
        paper['pdf_candidates'] = []
        manifest = self.run_download([paper])
        self.assertEqual(manifest['statistics']['attempted_papers'], 0)

    def test_successful_rerun_skips_network_and_rechecks_hash_parser(self):
        first = self.run_download()
        second = self.run_download()
        self.assertEqual(self.client.stream_pdf.call_count, 1)
        self.assertEqual(second['papers'][0]['download_status'], 'skipped_existing')
        self.assertEqual(second['papers'][0]['source'], first['papers'][0]['source'])
        self.assertEqual(second['statistics']['reused_existing_papers'], 1)
        self.assertEqual(len(second['papers'][0]['history']), 1)

    def test_same_paper_duplicate_record_downloads_once(self):
        manifest = self.run_download([target(), deepcopy(target())])
        self.assertEqual(self.client.stream_pdf.call_count, 1)
        self.assertEqual(len(list(self.root.glob('*.pdf'))), 1)
        self.assertEqual(manifest['papers'][1]['download_status'], 'skipped_existing')

    def test_different_urls_same_hash_share_file(self):
        first = dict(target(), doi=None, source_paper_id='W101')
        second = dict(target(), doi=None, source_paper_id='W102')
        second['pdf_candidates'] = [dict(second['pdf_candidates'][0], url='https://repo.example.edu/other')]
        second['best_pdf_url'] = second['pdf_candidates'][0]['url']
        manifest = self.run_download([first, second])
        self.assertEqual(self.client.stream_pdf.call_count, 2)
        self.assertEqual(len(list(self.root.glob('*.pdf'))), 1)
        self.assertEqual(manifest['papers'][1]['download_status'], 'duplicate_content')
        self.assertEqual(manifest['papers'][0]['local_filename'], manifest['papers'][1]['local_filename'])

    def test_same_hash_does_not_inherit_other_paper_identity_verification(self):
        first, second = target(), dict(target(), doi='10.1234/other')
        self.validator.validate.side_effect = lambda path, paper: PDFFileValidation(
            format_status='valid',
            identity_status='verified' if paper['doi'] == first['doi'] else 'rejected',
            validation_status='verified' if paper['doi'] == first['doi'] else 'rejected',
        )
        manifest = self.run_download([first, second])
        self.assertEqual(manifest['papers'][1]['download_status'], 'rejected_content')
        self.assertEqual(manifest['papers'][1]['identity_status'], 'rejected')
        self.assertEqual(manifest['statistics']['verified_papers'], 1)

    def test_changed_existing_checksum_triggers_new_file_without_overwrite(self):
        first = self.run_download()
        original = self.root / first['papers'][0]['local_filename']
        original.write_bytes(b'TAMPERED')
        second = self.run_download()
        self.assertEqual(self.client.stream_pdf.call_count, 2)
        self.assertEqual(original.read_bytes(), b'TAMPERED')
        self.assertNotEqual(first['papers'][0]['local_filename'], second['papers'][0]['local_filename'])
        self.assertTrue(second['papers'][0]['error_details'])

    def test_collision_suffix_still_respects_filename_byte_limit(self):
        config = replace(DEFAULT_DOWNLOAD, filename_bytes=64)
        paper = dict(target(), title='Very long scientific title ' * 100)
        downloader = PDFDownloader(self.client, self.validator, config)
        existing = self.root / paper_filename(paper, config)
        existing.write_bytes(b'user file')
        self.validator.validate.side_effect = lambda path, metadata: PDFFileValidation(
            format_status='invalid' if path.name == existing.name else 'valid',
            identity_status='verified',
            validation_status='verified',
        )
        manifest = downloader.download({'papers': [paper]}, self.root)
        self.assertEqual(existing.read_bytes(), b'user file')
        self.assertLessEqual(len(manifest['papers'][0]['local_filename'].encode('utf-8')), 64)

    def test_failed_request_retains_actual_url_and_does_not_claim_invalid_file(self):
        self.client.stream_pdf.side_effect = SourceError('HTTP 403', 403)
        manifest = self.run_download()
        entry = manifest['papers'][0]
        self.assertEqual(entry['pdf_url'], target()['pdf_candidates'][0]['url'])
        self.assertEqual(entry['format_status'], 'not_checked')
        self.assertEqual(entry['validation_status'], 'not_checked')
        self.assertEqual(entry['download_status'], 'failed')

    def test_unconfirmed_file_falls_back_to_a_confirmable_copy(self):
        downloader = PDFDownloader(per_url_client(), content_validator())
        entry = downloader.download({'papers': [two_copies()]}, self.root)['papers'][0]
        self.assertEqual(entry['validation_status'], 'verified')
        self.assertEqual(entry['pdf_source'], 'arxiv')
        self.assertEqual([attempt['status'] for attempt in entry['attempts']], ['unconfirmed_content', 'completed'])

    def test_unconfirmed_file_kept_when_no_copy_confirms(self):
        downloader = PDFDownloader(per_url_client(), content_validator(confirmed=b'nothing'))
        entry = downloader.download({'papers': [two_copies()]}, self.root)['papers'][0]
        self.assertEqual(entry['validation_status'], 'needs_review')
        self.assertEqual(entry['pdf_source'], 'europepmc')
        self.assertTrue((self.root / entry['local_filename']).is_file())

    def test_rerun_tries_other_urls_before_reusing_an_unconfirmed_file(self):
        client = per_url_client()
        paper = two_copies()
        paper['pdf_candidates'] = paper['pdf_candidates'][:1]
        PDFDownloader(client, content_validator()).download({'papers': [paper]}, self.root)
        second = PDFDownloader(client, content_validator()).download({'papers': [two_copies()]}, self.root)
        self.assertEqual(second['papers'][0]['validation_status'], 'verified')
        self.assertEqual(second['papers'][0]['pdf_source'], 'arxiv')

    def test_valid_format_uncertain_identity_stored_in_review_folder(self):
        self.downloader.validator = fake_validator('needs_review')
        manifest = self.run_download()
        entry = manifest['papers'][0]
        self.assertEqual(entry['download_status'], 'downloaded')
        self.assertEqual(entry['validation_status'], 'needs_review')
        self.assertEqual(entry['ownership_status'], 'verified')
        self.assertTrue(entry['local_filename'].startswith('needs_review/'))
        self.assertEqual(manifest['statistics']['verified_papers'], 0)

    def test_later_verified_file_promoted_without_redownloading(self):
        self.downloader.validator = fake_validator('needs_review')
        first = self.run_download()
        self.downloader.validator = fake_validator('verified')
        second = self.run_download()
        self.assertEqual(self.client.stream_pdf.call_count, 1)
        self.assertFalse(second['papers'][0]['local_filename'].startswith('needs_review/'))
        self.assertFalse((self.root / first['papers'][0]['local_filename']).exists())

    def test_invalid_pdf_removed_and_failure_persisted(self):
        self.downloader.validator = fake_validator('not_checked', 'invalid')
        manifest = self.run_download()
        entry = manifest['papers'][0]
        self.assertEqual(entry['download_status'], 'failed')
        self.assertEqual(entry['format_status'], 'invalid')
        self.assertIsNone(entry['local_filename'])
        self.assertTrue(entry['attempts'][0]['validation'])
        self.assertFalse(list(self.root.rglob('*.pdf')))
        self.assertFalse(list(self.root.glob('*.part')))

    def test_wrong_paper_quarantined_and_not_counted_success(self):
        self.downloader.validator = fake_validator('rejected')
        manifest = self.run_download()
        entry = manifest['papers'][0]
        self.assertEqual(entry['download_status'], 'rejected_content')
        self.assertTrue(entry['local_filename'].startswith('quarantine/'))
        self.assertEqual(manifest['statistics']['failed_papers'], 0)
        self.assertEqual(manifest['statistics']['rejected_content_papers'], 1)
        self.assertEqual(manifest['statistics']['verified_papers'], 0)

    def test_failed_url_falls_back_to_next_verified_candidate(self):
        paper = target()
        paper['pdf_candidates'].append(dict(paper['pdf_candidates'][0], url='https://repo.example.edu/fallback'))
        transfer = self.client.stream_pdf.side_effect
        calls = []

        def fail_first(url, path, config):
            calls.append(url)
            if len(calls) == 1:
                raise SourceError('HTTP 403', 403)
            return transfer(url, path, config)

        self.client.stream_pdf.side_effect = fail_first
        manifest = self.run_download([paper])
        entry = manifest['papers'][0]
        self.assertEqual(entry['download_status'], 'downloaded')
        self.assertEqual(entry['pdf_url'], 'https://repo.example.edu/fallback')
        self.assertEqual(entry['attempts'][0]['http_status'], 403)
        self.assertEqual(entry['attempts'][1]['status'], 'completed')

    def test_expired_final_url_can_fall_back_to_original_verified_locator(self):
        paper = target()
        paper['pdf_candidates'][0]['final_url'] = 'https://cdn.example.edu/expired'
        paper['best_pdf_url'] = paper['pdf_candidates'][0]['final_url']
        transfer = self.client.stream_pdf.side_effect

        def expire(url, path, config):
            if 'expired' in url:
                raise SourceError('HTTP 404', 404)
            return transfer(url, path, config)

        self.client.stream_pdf.side_effect = expire
        manifest = self.run_download([paper])
        self.assertEqual(self.client.stream_pdf.call_count, 2)
        self.assertEqual(manifest['papers'][0]['pdf_url'], 'https://repo.example.edu/document')
        self.assertEqual(manifest['papers'][0]['download_status'], 'downloaded')

    def test_failed_paper_does_not_stop_other_papers(self):
        first, second = target(), dict(target(), doi='10.1234/second')
        transfer = self.client.stream_pdf.side_effect
        calls = []

        def fail_first(url, path, config):
            calls.append(url)
            if len(calls) == 1:
                raise SourceError('timed out')
            return transfer(url, path, config)

        self.client.stream_pdf.side_effect = fail_first
        manifest = self.run_download([first, second])
        self.assertEqual(manifest['statistics']['failed_papers'], 1)
        self.assertEqual(manifest['statistics']['verified_papers'], 1)
        self.assertTrue(manifest['run_complete'])

    def test_max_downloads_marks_remainder_and_completed_do_not_consume_limit(self):
        papers = [target(), dict(target(), doi='10.1234/second')]
        first = self.run_download(papers, maximum_downloads=1)
        self.assertEqual(first['papers'][1]['download_status'], 'skipped_limit')
        second = self.run_download(papers, maximum_downloads=1)
        self.assertEqual(second['papers'][0]['download_status'], 'skipped_existing')
        self.assertEqual(second['papers'][1]['download_status'], 'duplicate_content')
        self.assertEqual(second['statistics']['attempted_papers'], 1)
        self.assertEqual(self.client.stream_pdf.call_count, 2)

    def test_interrupted_transfer_cleans_temp_and_manifest_allows_rerun(self):
        def interrupt(url, path, config):
            path.write_bytes(b'%PDF-partial')
            raise KeyboardInterrupt()

        self.client.stream_pdf.side_effect = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_download()
        saved = json.loads((self.root / 'manifest.json').read_text())
        self.assertFalse(saved['run_complete'])
        self.assertEqual(saved['papers'][0]['download_status'], 'interrupted')
        self.assertFalse(list(self.root.glob('*.part')))
        self.downloader.client = fake_client()
        self.assertEqual(self.run_download()['papers'][0]['download_status'], 'downloaded')

    def test_file_committed_before_manifest_recovers_without_network(self):
        (self.root / paper_filename(target())).write_bytes(b'%PDF-synthetic-fixture')
        manifest = self.run_download()
        self.client.stream_pdf.assert_not_called()
        self.assertEqual(manifest['papers'][0]['download_status'], 'skipped_existing')

    def test_previous_other_paper_records_preserved(self):
        self.run_download()
        manifest = self.run_download([dict(target(), doi='10.1234/second')])
        self.assertEqual(len(manifest['papers']), 2)
        self.assertEqual(manifest['statistics']['total_input_papers'], 1)

    def test_manifest_path_traversal_and_symlink_do_not_escape_folder(self):
        first = self.run_download()
        filename = first['papers'][0]['local_filename']
        (self.root / filename).unlink()
        first['papers'][0]['local_filename'] = '../../outside.pdf'
        (self.root / 'manifest.json').write_text(json.dumps(first))
        manifest = self.run_download()
        self.assertNotIn('..', manifest['papers'][0]['local_filename'])
        self.assertTrue(manifest['papers'][0]['error_details'])
        outside = self.root / 'outside.pdf'
        outside.write_bytes(b'preserve me')
        with self.assertRaises(ValueError):
            self.downloader._safe_path(self.root, str(outside))
        linked = self.root / 'link.pdf'
        linked.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.downloader._safe_path(self.root, 'link.pdf')
        self.assertEqual(outside.read_bytes(), b'preserve me')

    def test_bad_manifest_not_overwritten(self):
        path = self.root / 'manifest.json'
        path.write_text('[]')
        with self.assertRaises(ValueError):
            self.run_download()
        self.assertEqual(path.read_text(), '[]')

    def test_legacy_missing_fields_remain_missing(self):
        paper = target()
        del paper['authors']
        del paper['year']
        manifest = self.run_download([paper])
        self.assertEqual(manifest['papers'][0]['authors'], [])
        self.assertIsNone(manifest['papers'][0]['year'])

    def test_empty_archive_and_invalid_limit(self):
        manifest = self.downloader.download({'papers': []}, self.root)
        self.assertEqual(manifest['statistics']['total_input_papers'], 0)
        with self.assertRaises(ValueError):
            self.run_download(maximum_downloads=0)


@unittest.skipUnless(HAS_PYPDF, 'Install the download extra for integration test')
class DownloadIntegrationTests(unittest.TestCase):
    def test_actual_pdf_parser_download_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            client = fake_client(make_pdf())
            downloader = PDFDownloader(client, PDFFileValidator())
            first = downloader.download({'papers': [target()]}, directory)
            second = downloader.download({'papers': [target()]}, directory)
            self.assertEqual(first['statistics']['verified_papers'], 1)
            self.assertEqual(second['statistics']['reused_existing_papers'], 1)
            self.assertEqual(client.stream_pdf.call_count, 1)


class DownloadCLITests(unittest.TestCase):
    def test_cli_reads_only_url_json_and_creates_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'urls.json'
            output = Path(directory) / 'downloads'
            source.write_text(json.dumps({'papers': [target()]}))
            downloader = PDFDownloader(fake_client(), fake_validator())
            with (
                patch('paper_author_searcher.pdf_downloader.PDFDownloader', return_value=downloader),
                patch('builtins.print'),
            ):
                self.assertEqual(main(['--input', str(source), '--output-dir', str(output), '--max-downloads', '1']), 0)
            self.assertTrue((output / 'manifest.json').is_file())

    def test_cli_invalid_options_and_input_manifest_collision(self):
        for extra in (
            ['--timeout', '0'],
            ['--timeout', 'nan'],
            ['--retries', '-1'],
            ['--max-file-mb', '0'],
            ['--max-file-mb', 'inf'],
        ):
            with patch('sys.stderr'), self.assertRaises(SystemExit) as raised:
                main(['--input', 'urls.json', '--output-dir', 'downloaded', *extra])
            self.assertEqual(raised.exception.code, 2)
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            main(['--input', 'downloaded/manifest.json', '--output-dir', 'downloaded'])

    def test_cli_review_result_returns_two_and_keeps_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'urls.json'
            source.write_text(json.dumps({'papers': [target()]}))
            downloader = PDFDownloader(fake_client(), fake_validator('needs_review'))
            with (
                patch('paper_author_searcher.pdf_downloader.PDFDownloader', return_value=downloader),
                patch('builtins.print'),
            ):
                code = main(['--input', str(source), '--output-dir', str(Path(directory) / 'downloads')])
            self.assertEqual(code, 2)
