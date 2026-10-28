import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import patch

from paper_author_searcher.config import DEFAULT_DOWNLOAD
from paper_author_searcher.pdf_file_validation import PDFFileValidator, content_identity, inspect_pdf, validation_target
from pdf_fixtures import HAS_PYPDF, make_pdf


def target():
    return {
        'title': 'Reusable methods for synthetic research',
        'doi': '10.1234/example',
        'authors': ['Alex Example', 'Taylor Researcher'],
        'year': 2020,
    }


class ContentIdentityTests(unittest.TestCase):
    def publisher_candidate(self):
        return {
            'identity_status': 'verified',
            'validation_status': 'verified',
            'provenance': [
                {
                    'title': target()['title'],
                    'metadata_year': '2020',
                    'metadata_authors': ['Alex Example', 'Taylor Researcher'],
                }
            ],
        }

    def test_confirmed_publisher_bylines_can_corroborate_exact_pdf_title(self):
        saved = dict(target(), authors=['Alex M. Example', 'Taylor Researcher'])
        bound = validation_target(saved, self.publisher_candidate())
        front = saved['title'] + '\nAlex Example\nTaylor Researcher'
        identity, _, evidence = content_identity(bound, [], [front], front, '')
        self.assertEqual(identity, 'verified')
        self.assertTrue(any('publisher citation' in item for item in evidence))
        self.assertEqual(saved['authors'][0], 'Alex M. Example')

    def test_publisher_alternatives_need_title_year_and_full_byline_agreement(self):
        for changes in (
            {'metadata_year': '2021'},
            {'title': 'Other research'},
            {'metadata_authors': ['Unrelated Author']},
            {'doi': '10.9999/conflict'},
        ):
            candidate = self.publisher_candidate()
            candidate['provenance'][0].update(changes)
            self.assertEqual(validation_target(target(), candidate)['publisher_author_variants'], [])
        candidate = self.publisher_candidate()
        candidate['validation_status'] = 'unverified'
        self.assertEqual(validation_target(target(), candidate)['publisher_author_variants'], [])

    def test_publisher_variants_do_not_override_conflicting_pdf_doi(self):
        bound = validation_target(target(), self.publisher_candidate())
        front = target()['title'] + '\nAlex Example\nTaylor Researcher\n10.9999/conflict'
        self.assertEqual(content_identity(bound, [], [front], front, '')[0], 'needs_review')

    def test_unicode_doi_delimiters_are_not_part_of_identifier(self):
        saved = target()
        identity, dois, evidence = content_identity(saved, [saved['title']], [], '⟨10.1234/example⟩', '')
        self.assertEqual(identity, 'verified')
        self.assertEqual(dois, ['10.1234/example'])

    def test_a_label_glued_to_the_doi_is_not_part_of_it(self):
        saved = dict(target(), doi='10.1101/2023.12.14.571777')
        identity, dois, _ = content_identity(saved, [saved['title']], [], '10.1101/2023.12.14.571777doi: bioRxiv', '')
        self.assertEqual(identity, 'verified')
        self.assertEqual(dois, ['10.1101/2023.12.14.571777'])

    def test_the_works_arxiv_edition_counts_as_its_identifier(self):
        saved = dict(target(), doi='10.18653/v1/2025.findings-acl.1254', arxiv_ids=['2410.05613'])
        by_doi = content_identity(saved, [saved['title']], [], 'DOI: 10.48550/arXiv.2410.05613', '')
        self.assertEqual(by_doi[0], 'verified')
        self.assertTrue(any('arXiv identifier' in line for line in by_doi[2]))
        stamped = content_identity(saved, [saved['title']], [], 'arXiv:2410.05613v2 [cs.CL] 3 Oct 2024', '')
        self.assertEqual(stamped[0], 'verified')

    def test_another_works_arxiv_identifier_still_conflicts(self):
        saved = dict(target(), arxiv_ids=['2410.05613'])
        identity, _, evidence = content_identity(saved, [saved['title']], [], 'DOI: 10.48550/arXiv.2401.00001', '')
        self.assertEqual(identity, 'needs_review')
        self.assertTrue(any('DOI differs' in line for line in evidence))

    def test_validation_target_carries_the_works_arxiv_ids(self):
        paper = dict(target(), external_ids={'arxiv': '2410.05613'}, source='dblp', source_paper_id='x')
        self.assertEqual(validation_target(paper)['arxiv_ids'], ['2410.05613'])

    def test_validation_target_carries_related_editions_arxiv_ids(self):
        related = [{'identifiers': [{'source': 'dblp', 'paper_id': 'x', 'doi': '10.48550/arxiv.2410.05613'}]}]
        paper = dict(target(), paper_metadata={'related_versions': related})
        self.assertEqual(validation_target(paper)['arxiv_ids'], ['2410.05613'])

    def test_balanced_parentheses_inside_doi_preserved(self):
        saved = dict(target(), doi='10.1234/(abc)')
        identity, dois, evidence = content_identity(saved, [saved['title']], [], 'DOI: 10.1234/(abc).', '')
        self.assertEqual(identity, 'verified')
        self.assertEqual(dois, ['10.1234/(abc)'])

    def test_doi_in_references_without_matching_title_is_not_verified(self):
        saved = target()
        identity, dois, evidence = content_identity(saved, ['Unrelated paper'], [], saved['doi'], '')
        self.assertEqual(identity, 'needs_review')

    def test_title_substring_in_different_metadata_title_does_not_verify(self):
        saved = target()
        identity, dois, evidence = content_identity(
            saved, ['Review of ' + saved['title']], [saved['title']], saved['doi'], ''
        )
        self.assertEqual(identity, 'needs_review')


@unittest.skipUnless(HAS_PYPDF, 'Install the download extra for parser tests')
class PDFParsingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'document.part'

    def validate(self, body, saved=None, config=DEFAULT_DOWNLOAD):
        self.path.write_bytes(body)
        return inspect_pdf(self.path, saved or target(), config)

    def test_parseable_matching_pdf(self):
        result = self.validate(make_pdf())
        self.assertEqual(result.format_status, 'valid')
        self.assertEqual(result.identity_status, 'verified')
        self.assertEqual(result.page_count, 1)

    def test_wrong_paper_format_valid_identity_rejected(self):
        result = self.validate(make_pdf(title='Entirely unrelated findings', doi='10.5678/other'))
        self.assertEqual(result.format_status, 'valid')
        self.assertEqual(result.identity_status, 'rejected')

    def test_missing_doi_can_verify_distinctive_title_and_authors(self):
        result = self.validate(make_pdf(doi=None))
        self.assertEqual(result.format_status, 'valid')
        self.assertEqual(result.identity_status, 'verified')
        self.assertEqual(result.extracted_dois, [])

    def test_missing_doi_and_authors_requires_review(self):
        result = self.validate(make_pdf(doi=None, authors=''), dict(target(), authors=[]))
        self.assertEqual(result.format_status, 'valid')
        self.assertEqual(result.identity_status, 'needs_review')

    def test_generic_title_without_doi_not_auto_confirmed(self):
        result = self.validate(make_pdf(title='General methods', doi=None), dict(target(), title='General methods'))
        self.assertEqual(result.identity_status, 'needs_review')

    def test_different_doi_same_title_could_be_version_and_requires_review(self):
        result = self.validate(make_pdf(doi='10.5678/other'))
        self.assertEqual(result.identity_status, 'needs_review')

    def test_header_alone_does_not_pass_structure(self):
        self.assertEqual(self.validate(b'%PDF-1.7\nnot a document').format_status, 'invalid')

    def test_truncated_pdf_does_not_pass(self):
        self.assertEqual(self.validate(make_pdf()[:-30]).format_status, 'invalid')

    def test_html_is_invalid(self):
        self.assertEqual(self.validate(b'<html>sign in</html>').format_status, 'invalid')

    def test_bad_cross_reference_not_accepted(self):
        body = b'%PDF-1.7\n1 0 obj\n<< >>\nendobj\nstartxref\n9\n%%EOF\n'
        self.assertEqual(self.validate(body).format_status, 'invalid')

    def test_page_and_decompressed_content_limits(self):
        self.assertEqual(
            self.validate(make_pdf(pages=2), config=replace(DEFAULT_DOWNLOAD, maximum_pages=1)).format_status, 'invalid'
        )
        self.assertEqual(
            self.validate(make_pdf(), config=replace(DEFAULT_DOWNLOAD, maximum_page_content_bytes=8)).format_status,
            'invalid',
        )

    def test_javascript_is_observed_but_never_executed(self):
        result = self.validate(make_pdf(javascript='throw new Error("must never execute");'))
        self.assertEqual(result.format_status, 'valid')
        self.assertTrue(result.active_content_present)

    def test_worker_parses_actual_pdf(self):
        self.path.write_bytes(make_pdf())
        result = PDFFileValidator().validate(self.path, target())
        self.assertEqual(result.format_status, 'valid')
        self.assertEqual(result.identity_status, 'verified')

    def test_worker_timeout_is_not_format_success(self):
        with patch('paper_author_searcher.pdf_file_validation.subprocess.run', side_effect=TimeoutExpired('worker', 1)):
            result = PDFFileValidator().validate(self.path, target())
        self.assertEqual(result.format_status, 'unverified')
        self.assertEqual(result.identity_status, 'not_checked')
        self.assertTrue(result.error_details)
