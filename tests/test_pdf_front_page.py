"""A file whose text layer shows nothing: Claude transcribes its first page; the identity rules decide."""

import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pypdf import PdfWriter

from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.config import ExtractionConfig
from paper_author_searcher.http_budget import CostLedger
from paper_author_searcher.models import PDFFileValidation
from paper_author_searcher.pdf_file_validation import front_pages
from paper_author_searcher.pdf_front_page import (
    NOTHING_SEEN,
    READ_EVIDENCE,
    YEAR_DIFFERS,
    ClaudeFrontPageReader,
    FrontPage,
    FrontPageValidator,
    LayoutFrontPageReader,
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


def one_page_pdf(text):
    """A minimal PDF with one line of text on its page."""
    stream = f'BT /F1 12 Tf 72 720 Td ({text}) Tj ET'.encode()
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R '
        b'/Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length %d >>\nstream\n%s\nendstream' % (len(stream), stream),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    body, offsets = b'%PDF-1.4\n', []
    for number, content in enumerate(objects, 1):
        offsets.append(len(body))
        body += b'%d 0 obj\n%s\nendobj\n' % (number, content)
    start = len(body)
    body += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    body += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    return body + b'trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, start)


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
    name = 'test reader'

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def read(self, path):
        self.calls += 1
        return self.answer


def page_of(printed):
    if printed is None:
        return None
    return FrontPage((printed['title'] + '\n' + ', '.join(printed['authors']),), '\n'.join(printed['identifiers']))


def checked(result, answer, paper=PAPER):
    reader = Reader(page_of(answer))
    inner = SimpleNamespace(validate=lambda path, paper: result)
    return FrontPageValidator(inner, [reader]).validate('x.pdf', paper), reader


class ValidatorTests(unittest.TestCase):
    def test_a_printed_title_and_authors_confirm_the_file(self):
        result, _ = checked(unseen(), PRINTED)
        self.assertEqual(result.validation_status, PDFValidationStatus.VERIFIED)
        self.assertIn(READ_EVIDENCE.format(reader='test reader'), result.evidence)

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

    def test_a_later_reader_gets_its_turn_only_if_the_earlier_ones_fail(self):
        inner = SimpleNamespace(validate=lambda path, paper: unseen())
        first, second = Reader(page_of({**PRINTED, 'title': 'Something else'})), Reader(page_of(PRINTED))
        result = FrontPageValidator(inner, [first, second]).validate('x.pdf', PAPER)
        self.assertEqual(result.validation_status, PDFValidationStatus.VERIFIED)
        self.assertEqual((first.calls, second.calls), (1, 1))
        third = Reader(page_of(PRINTED))
        FrontPageValidator(inner, [second, third]).validate('x.pdf', PAPER)
        self.assertEqual(third.calls, 0)

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
            self.assertEqual(reader.transcribe(b'pages')['title'], 'T')
            self.assertEqual(reader.transcribe(b'pages')['title'], 'T')
            self.assertEqual(client.beta.messages.create.call_count, 1)
            self.assertAlmostEqual(ledger.spent_usd, (4000 + 2000) / 1_000_000)

    def test_a_spent_budget_or_a_refusal_gives_no_answer(self):
        spent = CostLedger(0.0)
        self.assertIsNone(ClaudeFrontPageReader(ExtractionConfig(), client_answering(), ledger=spent).transcribe(b'x'))
        refused = client_answering(stop='refusal')
        self.assertIsNone(ClaudeFrontPageReader(ExtractionConfig(), refused).transcribe(b'x'))

    def test_api_failures_give_no_answer(self):
        client = mock.Mock()
        client.beta.messages.create.side_effect = RuntimeError('down')
        self.assertIsNone(ClaudeFrontPageReader(ExtractionConfig(), client).transcribe(b'x'))


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


class LayoutReaderTests(unittest.TestCase):
    def test_a_real_page_is_read_under_each_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'paper.pdf'
            path.write_bytes(one_page_pdf('Learning the structure of deep models'))
            page = LayoutFrontPageReader(({}, {'boxes_flow': None})).read(path)
            self.assertEqual(len(page.regions), 2)
            self.assertIn('Learning the structure of deep models', page.regions[1])

    def test_an_unreadable_file_gives_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'broken.pdf'
            path.write_bytes(b'not a pdf')
            self.assertIsNone(LayoutFrontPageReader(({},)).read(path))


class WiringTests(unittest.TestCase):
    def test_pdfminer_reads_by_default_and_claude_only_when_switched_on(self):
        base = Settings()
        names = [r.name for r in ServiceContainer(base, RunOptions()).downloader.validator.readers]
        self.assertEqual(names, ['pdfminer'])
        keyed = replace(base, credentials=mock.Mock(anthropic_api_key='k'))
        names = [r.name for r in ServiceContainer(keyed, RunOptions()).downloader.validator.readers]
        self.assertEqual(names, ['pdfminer'])
        extraction = replace(base.extraction, read_front_pages=True)
        on = replace(keyed, extraction=extraction)
        names = [r.name for r in ServiceContainer(on, RunOptions()).downloader.validator.readers]
        self.assertEqual(names, ['pdfminer', 'Claude'])

    def test_extraction_needs_switching_on_even_with_a_key(self):
        keyed = replace(Settings(), credentials=mock.Mock(anthropic_api_key='k'))
        self.assertIsNone(ServiceContainer(keyed, RunOptions()).extraction_completion)


if __name__ == '__main__':
    unittest.main()
