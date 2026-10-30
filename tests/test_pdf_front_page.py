"""A file whose text layer shows nothing: Claude transcribes its first page; the identity rules decide."""

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pypdf import PdfWriter

from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.config import ExtractionConfig
from paper_author_searcher.http_budget import CostLedger
from paper_author_searcher.models import PDFFileValidation
from paper_author_searcher.pdf_file_validation import PDFFileValidator, front_pages
from paper_author_searcher.pdf_front_page import (
    NOTHING_SEEN,
    READ_EVIDENCE,
    YEAR_DIFFERS,
    ClaudeFrontPageReader,
    FrontPageValidator,
)
from paper_author_searcher.settings import Settings
from paper_author_searcher.status import FormatStatus, IdentityStatus, PDFValidationStatus

PAPER = {
    'title': 'Learning the structure of deep sparse graphical models',
    'authors': ['Ada Lovelace', 'Charles Babbage'],
    'doi': None,
    'year': 2024,
}
PRINTED = {
    'title': 'Learning the Structure of Deep Sparse Graphical Models',
    'authors': ['Ada Lovelace', 'Charles Babbage'],
    'identifiers': [],
}


def unseen(**changes):
    values = {
        'format_status': FormatStatus.VALID,
        'identity_status': IdentityStatus.NEEDS_REVIEW,
        'validation_status': PDFValidationStatus.NEEDS_REVIEW,
        'document_type': 'full_text',
        'evidence': [NOTHING_SEEN],
    }
    return PDFFileValidation(**{**values, **changes})


class Reader:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def read(self, pages):
        self.calls += 1
        return self.answer


def checked(result, answer, paper=PAPER):
    reader = Reader(answer)
    inner = SimpleNamespace(validate=lambda path, paper: result)
    with mock.patch('paper_author_searcher.pdf_front_page.front_pages', return_value=b'%PDF'):
        return FrontPageValidator(inner, reader).validate('x.pdf', paper), reader


class ValidatorTests(unittest.TestCase):
    def test_a_printed_title_and_authors_confirm_the_file(self):
        result, _ = checked(unseen(), PRINTED)
        self.assertEqual(result.validation_status, PDFValidationStatus.VERIFIED)
        self.assertIn(READ_EVIDENCE, result.evidence)

    def test_another_papers_title_confirms_nothing(self):
        result, _ = checked(unseen(), {**PRINTED, 'title': 'A survey of something else entirely here'})
        self.assertEqual(result.validation_status, PDFValidationStatus.NEEDS_REVIEW)

    def test_the_right_title_without_its_authors_confirms_nothing(self):
        result, _ = checked(unseen(), {**PRINTED, 'authors': ['Somebody Else']})
        self.assertEqual(result.validation_status, PDFValidationStatus.NEEDS_REVIEW)

    def test_a_conflicting_metadata_title_still_blocks(self):
        result, _ = checked(unseen(extracted_titles=['A wholly different paper about other matters']), PRINTED)
        self.assertEqual(result.validation_status, PDFValidationStatus.NEEDS_REVIEW)

    def test_files_held_back_for_other_reasons_are_not_read(self):
        for result in (
            unseen(evidence=['Front-matter DOI differs; this can also reflect a different version']),
            unseen(document_type='poster'),
            unseen(evidence=[NOTHING_SEEN, YEAR_DIFFERS]),
            unseen(format_status=FormatStatus.INVALID),
            unseen(identity_status=IdentityStatus.VERIFIED, validation_status=PDFValidationStatus.VERIFIED),
        ):
            answer, reader = checked(result, PRINTED)
            self.assertIs(answer, result)
            self.assertEqual(reader.calls, 0)

    def test_no_answer_leaves_the_file_as_it_was(self):
        result = unseen()
        self.assertIs(checked(result, None)[0], result)


def client_answering(text='{"title": "T", "authors": [], "identifiers": []}', stop='end_turn'):
    client = mock.Mock()
    client.beta.messages.create.return_value = SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type='text', text=text)],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
    )
    return client


class ReaderTests(unittest.TestCase):
    def test_answers_are_cached_by_content_and_spend_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            client = client_answering()
            ledger = CostLedger(1.0)
            reader = ClaudeFrontPageReader(ExtractionConfig(), client, ledger=ledger, cache_directory=directory)
            self.assertEqual(reader.read(b'pages')['title'], 'T')
            self.assertEqual(reader.read(b'pages')['title'], 'T')
            self.assertEqual(client.beta.messages.create.call_count, 1)
            self.assertAlmostEqual(ledger.spent_usd, (4000 + 2000) / 1_000_000)

    def test_a_spent_budget_or_a_refusal_gives_no_answer(self):
        spent = CostLedger(0.0)
        self.assertIsNone(ClaudeFrontPageReader(ExtractionConfig(), client_answering(), ledger=spent).read(b'x'))
        refused = client_answering(stop='refusal')
        self.assertIsNone(ClaudeFrontPageReader(ExtractionConfig(), refused).read(b'x'))

    def test_api_failures_give_no_answer(self):
        client = mock.Mock()
        client.beta.messages.create.side_effect = RuntimeError('down')
        self.assertIsNone(ClaudeFrontPageReader(ExtractionConfig(), client).read(b'x'))


class FrontPagesTests(unittest.TestCase):
    def test_only_the_first_pages_are_kept(self):
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'long.pdf'
            writer = PdfWriter()
            for _ in range(5):
                writer.add_blank_page(100, 100)
            with path.open('wb') as handle:
                writer.write(handle)
            self.assertEqual(len(PdfReader(io.BytesIO(front_pages(path, 2))).pages), 2)
            self.assertIsNone(front_pages(Path(directory) / 'missing.pdf', 1))


class WiringTests(unittest.TestCase):
    def test_the_downloader_reads_front_pages_only_with_a_key(self):
        plain = ServiceContainer(Settings(), RunOptions()).downloader
        self.assertIsInstance(plain.validator, PDFFileValidator)
        base = Settings()
        keyed = base.__class__(**{**vars(base), 'credentials': mock.Mock(anthropic_api_key='k')})
        self.assertIsInstance(ServiceContainer(keyed, RunOptions()).downloader.validator, FrontPageValidator)


if __name__ == '__main__':
    unittest.main()
