import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_author_searcher.pdf_discovery_comparison import compare_runs, summarize_runs


def paper(title='A general method', verified=True):
    return {
        'title': title,
        'year': 2020,
        'authors': ['Example Author'],
        'ownership_status': 'verified',
        'validation_status': 'verified' if verified else 'not_found',
        'pdf_candidates': [
            {
                'url': 'https://repo.example.edu/' + title + '.pdf',
                'source': 'public_api',
                'validation_status': 'verified',
                'identity_status': 'verified',
            }
        ]
        if verified
        else [],
    }


class ComparisonTests(unittest.TestCase):
    def write_run(self, root, name, papers, manifests=None):
        path = root / name
        path.mkdir()
        (path / 'pdf_urls.json').write_text(json.dumps({'papers': papers}), encoding='utf-8')
        if manifests is not None:
            (path / 'manifest.json').write_text(json.dumps({'papers': manifests}), encoding='utf-8')
        return path

    def test_multiple_baselines_union_without_double_counting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_run(root, 'first', [paper(), paper('Other', False)])
            second = self.write_run(root, 'second', [paper(), paper('Other')])
            value = summarize_runs([first, second])
            self.assertEqual(value['cohort_size'], 2)
            self.assertEqual(value['verified_pdf_url_coverage']['rate'], 1)
            self.assertEqual(value['distinct_verified_urls'], 2)
            self.assertEqual(value['source_verified_paper_contributions']['public_api'], 2)
            self.assertEqual(value['content_confirmed_coverage']['numerator'], 0)

    def test_url_signature_without_identity_is_not_confirmed(self):
        entry = paper()
        entry['pdf_candidates'][0]['identity_status'] = 'unverified'
        with tempfile.TemporaryDirectory() as directory:
            root = self.write_run(Path(directory), 'run', [entry])
            self.assertEqual(summarize_runs([root])['verified_pdf_url_coverage']['numerator'], 0)

    def test_different_cohorts_cannot_be_compared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_run(root, 'first', [paper()])
            second = self.write_run(root, 'second', [paper('Other')])
            with self.assertRaisesRegex(ValueError, 'cohorts differ'):
                compare_runs([first], [second])

    def test_missing_file_is_not_counted_as_content_confirmation(self):
        entry = dict(
            paper(),
            download_status='downloaded',
            format_status='valid',
            identity_status='verified',
            local_filename='nonexistent.pdf',
            sha256='bad',
            file_size=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = self.write_run(Path(directory), 'run', [paper()], [entry])
            value = summarize_runs([root])
            self.assertEqual(value['download_attempted_papers'], 1)
            self.assertEqual(value['content_confirmed_coverage']['numerator'], 0)
            self.assertFalse(value['file_checks'][0]['file_integrity_confirmed'])

    def test_preserved_doi_does_not_hide_changed_year_or_ownership(self):
        first_entry = dict(paper(), doi='10.1234/method')
        changed_entry = dict(first_entry, year=2021)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_run(root, 'first', [first_entry])
            second = self.write_run(root, 'second', [changed_entry])
            with self.assertRaisesRegex(ValueError, 'metadata or ownership'):
                compare_runs([first], [second])

    def test_format_valid_does_not_imply_content_verified(self):
        entry = dict(
            paper(),
            download_status='downloaded',
            format_status='valid',
            identity_status='needs_review',
            validation_status='needs_review',
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                'paper_author_searcher.pdf_discovery_comparison._check_file', return_value=(True, 'verified checksum')
            ),
        ):
            root = self.write_run(Path(directory), 'run', [paper()], [entry])
            value = summarize_runs([root])
            self.assertEqual(value['valid_downloaded_file_coverage']['numerator'], 1)
            self.assertEqual(value['content_confirmed_coverage']['numerator'], 0)
            self.assertEqual(value['content_needs_review_papers'], 1)

    def test_coverage_delta_and_lost_urls_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.write_run(root, 'first', [paper(), paper('Other', False)])
            second = self.write_run(root, 'second', [paper(verified=False), paper('Other')])
            value = compare_runs([first], [second])
            self.assertEqual(value['url_coverage_change'], 0)
            self.assertEqual(len(value['newly_verified_pdf_paper_keys']), 1)
            self.assertEqual(len(value['lost_verified_pdf_paper_keys']), 1)

    def test_cooldown_partial_and_disabled_sources_remain_incomplete(self):
        entries = [
            dict(paper(str(index)), providers=[{'source': 'public_pages', 'status': status}])
            for index, status in enumerate(('partial', 'disabled', 'not_found', 'ok'))
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = self.write_run(Path(directory), 'run', entries)
            self.assertEqual(summarize_runs([root])['search_completion_rate']['numerator'], 2)

    def test_poster_is_valid_file_but_not_confirmed_complete_paper(self):
        entry = dict(
            paper(),
            download_status='non_full_document',
            format_status='valid',
            identity_status='verified',
            validation_status='needs_review',
            document_type='poster',
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                'paper_author_searcher.pdf_discovery_comparison._check_file', return_value=(True, 'verified checksum')
            ),
        ):
            root = self.write_run(Path(directory), 'run', [paper()], [entry])
            value = summarize_runs([root])
            self.assertEqual(value['valid_downloaded_file_coverage']['numerator'], 1)
            self.assertEqual(value['content_confirmed_coverage']['numerator'], 0)
            self.assertEqual(value['full_text_confirmed_coverage']['numerator'], 0)
            self.assertEqual(value['needs_review_ratio_among_valid_files']['rate'], 1)

    def test_legacy_missing_classification_is_not_reported_as_zero_full_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.write_run(Path(directory), 'run', [paper()])
            self.assertIsNone(summarize_runs([root])['full_text_confirmed_coverage'])
