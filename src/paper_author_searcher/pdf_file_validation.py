"""Parse PDFs in a bounded worker; distinguish file structure from paper identity."""

import html
import importlib.util
import json
import logging
import re
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import DEFAULT_DOWNLOAD, DownloadConfig
from .http import has_pdf_signature
from .matching import arxiv_from_doi, canonical_arxiv, canonical_doi, normalize, normalize_title
from .models import PDFFileValidation
from .pdf_document_types import NON_FULL_DOCUMENTS, classify_document
from .pdf_front_matter import authors_present, front_matter, metadata_title_kind, publication_years, title_in_region
from .pdf_identity import arxiv_id, edition_arxiv_ids, metadata_identity
from .status import FormatStatus, IdentityStatus, PDFValidationStatus

logger = logging.getLogger(__name__)


def validation_target(paper, candidate=None):
    """Preserve publisher bylines only after the URL's citation metadata was bound."""
    target = {key: paper.get(key) for key in ('title', 'authors', 'year', 'doi')}
    # Another edition of the same work (its arXiv preprint) prints the arXiv identifier, not the venue DOI.
    target['arxiv_ids'] = edition_arxiv_ids(paper)
    target['served_arxiv_id'], target['edition_titles'] = _served_edition(candidate)
    target['pdf_version_type'] = candidate.get('version_type') if candidate else None
    target['venue'] = paper.get('venue') or (paper.get('paper_metadata') or {}).get('venue')
    variants = []
    if (
        candidate
        and candidate.get('identity_status') == IdentityStatus.VERIFIED
        and candidate.get('validation_status') == PDFValidationStatus.VERIFIED
    ):
        for provenance in candidate.get('provenance') or []:
            authors = provenance.get('metadata_authors') or []
            title = provenance.get('title') or provenance.get('metadata_title')
            year = provenance.get('metadata_year')
            if not authors or not target['year'] or str(year) != str(target['year']):
                continue
            if normalize_title(title or '') != normalize_title(target['title'] or ''):
                continue
            identity, _ = metadata_identity(
                paper, 'landing_page', doi=provenance.get('doi'), title=title, year=year, authors=authors
            )
            signature_identity, _ = metadata_identity(paper, 'landing_page', title=title, year=year, authors=authors)
            if (
                identity == IdentityStatus.VERIFIED
                and signature_identity == IdentityStatus.VERIFIED
                and authors not in variants
            ):
                variants.append(authors)
    target['publisher_author_variants'] = variants
    target['publication_bindings'] = []
    if (
        candidate
        and candidate.get('identity_status') == IdentityStatus.VERIFIED
        and candidate.get('validation_status') == PDFValidationStatus.VERIFIED
    ):
        for provenance in candidate.get('provenance') or []:
            title = provenance.get('title') or provenance.get('metadata_title')
            if normalize_title(title or '') != normalize_title(target['title'] or ''):
                continue
            target['publication_bindings'].append(
                {
                    key: provenance.get(key)
                    for key in (
                        'provider',
                        'metadata_url',
                        'source_paper_id',
                        'doi',
                        'metadata_year',
                        'metadata_authors',
                        'metadata_venue',
                    )
                }
            )
    return target


def _served_edition(candidate):
    """The arXiv ID a bound URL was served under, and the titles arXiv gave that ID.

    arXiv serves one paper at arxiv.org/pdf/<id>; once the URL was bound to the paper (its identity verified), the
    identifier the file came from is evidence of the edition, though no DOI or margin stamp survives extraction. A
    preprint renamed for its venue edition prints the arXiv title, so that title is accepted for this file too.
    """
    if (
        not candidate
        or candidate.get('identity_status') != IdentityStatus.VERIFIED
        or candidate.get('validation_status') != PDFValidationStatus.VERIFIED
    ):
        return None, []
    served = canonical_arxiv(arxiv_id(candidate.get('final_url') or candidate.get('url')) or '')
    if not served:
        return None, []
    titles = []
    for provenance in candidate.get('provenance') or []:
        title = provenance.get('metadata_title') or provenance.get('title')
        if title and canonical_arxiv(str(provenance.get('source_paper_id') or '')) == served and title not in titles:
            titles.append(title)
    return served, titles


def _best_title_kind(title, expected_titles, minimum_words):
    kinds = [metadata_title_kind(title, expected, minimum_words) for expected in expected_titles]
    return next((kind for kind in ('matching', 'decorated', 'technical') if kind in kinds), 'conflicting')


def content_identity(paper, titles, title_regions, text, author_metadata, config=DEFAULT_DOWNLOAD):
    expected_title = normalize_title(paper.get('title') or '')
    expected_titles = [paper.get('title') or '', *(paper.get('edition_titles') or [])]
    titles = [_without_artifacts(title, config) for title in titles]
    metadata_match = any(
        normalize_title(title) in {normalize_title(value) for value in expected_titles} for title in titles
    )
    metadata_kinds = [_best_title_kind(title, expected_titles, config.metadata_title_minimum_words) for title in titles]
    normal_title_match = metadata_match
    title_match = metadata_match
    for region in title_regions:
        for expected in expected_titles:
            if title_in_region(expected, region, compact=False):
                normal_title_match = True
            title_match = title_match or title_in_region(expected, region)
    observed_dois = set()
    for value in re.findall(r'10\.\d{4,9}/[^\s<>"\']+', text, flags=re.IGNORECASE):
        observed_dois.add(canonical_doi(_clean_extracted_doi(value, config.doi_glued_labels))[len('doi:') :])
    expected_doi = canonical_doi(paper['doi'])[len('doi:') :] if paper.get('doi') else None
    # The work's own editions: its DOI, and its arXiv preprint (printed as an arXiv DOI or as "arXiv:ID").
    served = paper.get('served_arxiv_id')
    editions = ({canonical_arxiv(value) for value in paper.get('arxiv_ids') or []} | {served}) - {None}
    observed_editions = {arxiv_from_doi(doi) for doi in observed_dois} | set(_printed_arxiv_ids(text)) | {served}
    edition_match = bool(editions & (observed_editions - {None}))
    doi_match = bool(expected_doi and expected_doi in observed_dois) or edition_match
    title_conflict = 'conflicting' in metadata_kinds
    evidence = []
    if title_match:
        evidence.append('Expected normalized title occurs in PDF title metadata or the first-page title region')
    if doi_match and not edition_match:
        evidence.append('Target DOI found in the downloaded PDF front matter/metadata')
    if edition_match and served and served in editions & observed_editions - set(_printed_arxiv_ids(text)):
        evidence.append("arXiv served this file under the work's own arXiv identifier, bound to the paper")
    elif edition_match:
        evidence.append("The work's own arXiv identifier is printed in the PDF (an edition of the same work)")
    if title_conflict:
        evidence.append('PDF title metadata differs from the target; content association remains conservative')
    if 'technical' in metadata_kinds:
        evidence.append('Technical filename/producer title is not a scientific-title conflict')
    if 'decorated' in metadata_kinds:
        evidence.append('Bibliographic series prefix is separated from the scientific title')
    authors_match = _authors_present(paper.get('authors') or [], title_regions, author_metadata)
    publisher_match = False
    for authors in paper.get('publisher_author_variants') or []:
        if _authors_present(authors, title_regions, author_metadata):
            authors_match = True
            publisher_match = True
            break
    if title_match and doi_match and not title_conflict and (normal_title_match or authors_match):
        return IdentityStatus.VERIFIED, sorted(observed_dois), evidence
    long_title = len(expected_title.split()) >= config.minimum_title_words_without_doi
    conflicting_doi = bool(expected_doi and observed_dois and not doi_match)
    if title_match and long_title and authors_match and not conflicting_doi and not title_conflict:
        evidence.append('Full expected author signatures corroborate a distinctive title without requiring DOI')
        if publisher_match:
            evidence.append(
                'All bylines from the independently bound publisher citation metadata occur in PDF front matter'
            )
        return IdentityStatus.VERIFIED, sorted(observed_dois), evidence
    if conflicting_doi:
        evidence.append('Front-matter DOI differs; this can also reflect a different version')
        if titles and not title_match:
            evidence.append('Explicit PDF title also differs from the target paper')
            return IdentityStatus.REJECTED, sorted(observed_dois), evidence
    evidence.append('File is parseable, but its correspondence to the target paper requires review')
    return IdentityStatus.NEEDS_REVIEW, sorted(observed_dois), evidence


def _without_artifacts(title, config):
    """A PDF title with typesetting remnants removed ("[width=2em,height=2em]assets/logo.png AxBench: ...")."""
    title = html.unescape(title)
    return ' '.join(re.sub(config.title_artifact_pattern, ' ', title, flags=re.IGNORECASE).split())


def _printed_arxiv_ids(text):
    """arXiv identifiers printed as "arXiv:2408.15126v6" (the preprint's margin stamp or citation line)."""
    found = re.findall(r'arxiv\s*:\s*(\d{4}\.\d{4,5})(?:v\d+)?', text, flags=re.IGNORECASE)
    return [identifier for identifier in (canonical_arxiv(value) for value in found) if identifier]


def _clean_extracted_doi(value, glued_labels=()):
    value = _strip_trailing_punctuation(value)
    for label in glued_labels:
        if len(value) > len(label) and value.lower().endswith(label) and value[-len(label) - 1].isdigit():
            return _strip_trailing_punctuation(value[: -len(label)])
    return value


def _strip_trailing_punctuation(value):
    pairs = {')': '(', ']': '[', '}': '{'}
    while value:
        last = value[-1]
        if last in pairs and value.count(pairs[last]) >= value.count(last):
            break
        closing = unicodedata.category(last) in ('Pe', 'Pf')
        if last not in '.,;:' and not closing:
            break
        value = value[:-1]
    return value


def _authors_present(expected_authors, title_regions, author_metadata):
    return authors_present(expected_authors, title_regions, author_metadata)


def inspect_pdf(path, paper, config=DEFAULT_DOWNLOAD):
    from pypdf import PdfReader

    result = PDFFileValidation(identity_status=IdentityStatus.NOT_CHECKED)
    try:
        with open(path, 'rb') as handle:
            handle.seek(0, 2)
            size = handle.tell()
            if not 0 < size <= config.maximum_file_bytes:
                raise ValueError('PDF file size is outside the configured limit')
            handle.seek(0)
            if not has_pdf_signature(handle.read(1024)):
                raise ValueError('PDF file signature is missing')
            handle.seek(max(0, size - config.footer_scan_bytes))
            footer = handle.read(config.footer_scan_bytes)
            ending = re.search(rb'startxref\s+(\d+)\s+%%EOF\s*$', footer)
            if not ending or not 0 < int(ending.group(1)) < size:
                raise ValueError('PDF cross-reference/EOF structure is absent or truncated')
            handle.seek(0)
            reader = PdfReader(handle, strict=True)
            if reader.is_encrypted:
                raise ValueError('Encrypted PDFs are not decrypted or bypassed')
            result.page_count = len(reader.pages)
            if not 0 < result.page_count <= config.maximum_pages:
                raise ValueError('PDF page count is outside the configured limit')
            metadata: dict[str, Any] = reader.metadata or {}
            title = str(metadata.get('/Title') or '')[: config.maximum_extracted_characters]
            author = str(metadata.get('/Author') or '')[: config.maximum_extracted_characters]
            if title:
                result.extracted_titles.append(title)
            text_parts = []
            title_regions = []
            for index, page in enumerate(reader.pages):
                contents = page.get_contents()
                if contents is not None and len(contents.get_data()) > config.maximum_page_content_bytes:
                    raise ValueError('Decoded page content exceeds the configured limit')
                if index >= config.identity_pages:
                    continue
                try:
                    text = (page.extract_text() or '')[: config.maximum_extracted_characters]
                    text_parts.append(text)
                    if index == 0:
                        title_regions.append(front_matter('\n'.join(text.splitlines()[: config.title_lines])))
                except Exception as exc:
                    logger.debug('PDF text extraction unavailable for %s', path, exc_info=True)
                    result.error_details.append('Text extraction unavailable: ' + str(exc)[:1000])
            root: Any = reader.trailer['/Root']  # pypdf types this as a bare PdfObject
            names = root.get('/Names')
            result.active_content_present = bool(root.get('/OpenAction') or root.get('/AA'))
            if names is not None and '/JavaScript' in names.get_object():
                result.active_content_present = True
            metadata_text = ' '.join(
                str(metadata.get(key) or '') for key in ('/Title', '/Subject', '/Keywords', '/DOI')
            )
            first_page = text_parts[0] if text_parts else ''
            # Reference-list DOIs and body citations cannot establish this file's identity.
            identity_text = front_matter(first_page) + '\n' + metadata_text
            identity, dois, evidence = content_identity(
                paper, result.extracted_titles, title_regions, identity_text, author, config
            )
            first_size = [float(reader.pages[0].mediabox.width), float(reader.pages[0].mediabox.height)]
            document_type, document_evidence = classify_document(
                metadata, first_page, result.page_count, first_size, paper.get('pdf_version_type'), config
            )
            result.document_type = document_type
            result.document_type_evidence = document_evidence
            result.observations = {
                'first_page_front_matter': front_matter(first_page)[: config.maximum_extracted_characters],
                'publication_years': publication_years(first_page),
                'expected_year': paper.get('year'),
                'metadata_title_kinds': [
                    metadata_title_kind(value, paper.get('title') or '', config.metadata_title_minimum_words)
                    for value in result.extracted_titles
                ],
                'publication_bindings': paper.get('publication_bindings') or [],
                'full_author_signatures_present': _authors_present(paper.get('authors') or [], title_regions, author),
                'expected_venue': paper.get('venue'),
                'venue_present_in_first_page': bool(
                    paper.get('venue') and normalize(paper['venue']) in normalize(first_page)
                ),
            }
            if paper.get('year') and str(paper['year']) in result.observations['publication_years']:
                evidence.append('Publication year in PDF agrees with the saved publication record')
            elif result.observations['publication_years']:
                evidence.append('Observed publication year differs or represents another version; retained for review')
                if (
                    identity == IdentityStatus.VERIFIED
                    and paper.get('year')
                    and paper.get('pdf_version_type') != 'preprint'
                ):
                    identity = IdentityStatus.NEEDS_REVIEW
            if result.observations['venue_present_in_first_page']:
                evidence.append('Saved publication venue also occurs on the PDF first page; auxiliary evidence only')
            result.format_status = FormatStatus.VALID
            result.identity_status = identity
            result.validation_status = PDFValidationStatus(identity)
            if document_type in NON_FULL_DOCUMENTS and identity != IdentityStatus.REJECTED:
                result.validation_status = PDFValidationStatus.NEEDS_REVIEW
                evidence.append('Non-full-text document is not counted as a confirmed complete paper')
            result.extracted_dois = dois
            result.evidence = [
                'Strict PDF parser resolved pages and page content streams; EOF and startxref checked',
                *evidence,
            ]
    except Exception as exc:
        logger.debug('PDF structure validation failed for %s', path, exc_info=True)
        result.format_status = FormatStatus.INVALID
        result.validation_status = PDFValidationStatus.INVALID
        result.error_details.append(str(exc)[:1000])
    return result


# The parser worker imports this package from the same location as its parent however it was installed (an
# ignored .pth, e.g. a macOS-hidden editable install, must not silently disable parsing). argv: root, pdf path.
PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])
WORKER_BOOTSTRAP = (
    'import runpy, sys; root = sys.argv.pop(1); sys.path.insert(0, root); '
    "runpy.run_module('paper_author_searcher.pdf_file_validation', run_name='__main__')"
)


class PDFFileValidator:
    def __init__(self, config=DEFAULT_DOWNLOAD):
        self.config = config
        if importlib.util.find_spec('pypdf') is None:
            raise RuntimeError('Install the download extra: pip install -e ".[download]"')

    def validate(self, path, paper):
        target = {
            key: paper.get(key)
            for key in (
                'title',
                'authors',
                'year',
                'doi',
                'arxiv_ids',
                'served_arxiv_id',
                'edition_titles',
                'venue',
                'publisher_author_variants',
                'pdf_version_type',
                'publication_bindings',
            )
        }
        request = json.dumps({'paper': target, 'config': asdict(self.config)})
        try:
            completed = subprocess.run(
                [sys.executable, '-c', WORKER_BOOTSTRAP, PACKAGE_ROOT, str(path)],
                input=request,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=self.config.parser_timeout,
                check=False,
            )
            if completed.returncode != 0:
                raise ValueError('PDF parser worker failed or exceeded its resource limit')
            return PDFFileValidation(**json.loads(completed.stdout))
        except (subprocess.TimeoutExpired, OSError, ValueError, TypeError) as exc:
            return PDFFileValidation(
                identity_status=IdentityStatus.NOT_CHECKED,
                validation_status=PDFValidationStatus.UNVERIFIED,
                error_details=[str(exc)[:1000]],
            )


def extract_pages(path, destination, pages):
    """Copy a PDF's first ``pages`` pages to ``destination`` (a parser worker's job: the file is untrusted)."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(path, strict=False)
    writer = PdfWriter()
    for page in reader.pages[:pages]:
        writer.add_page(page)
    with open(destination, 'wb') as handle:
        writer.write(handle)


def front_pages(path, pages, config=DEFAULT_DOWNLOAD):
    """The PDF's first pages as bytes, cut in a resource-limited worker; None when it cannot be done."""
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory) / 'front.pdf'
        request = json.dumps({'config': asdict(config), 'extract_to': str(destination), 'pages': pages})
        try:
            subprocess.run(
                [sys.executable, '-c', WORKER_BOOTSTRAP, PACKAGE_ROOT, str(path)],
                input=request,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=config.parser_timeout,
                check=True,
            )
            return destination.read_bytes()
        except (subprocess.SubprocessError, OSError):
            return None


def _apply_worker_limits(config):
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (config.parser_memory_bytes, config.parser_memory_bytes))
    except (ImportError, ValueError, OSError):
        pass


def _worker():
    request = json.load(sys.stdin)
    config = DownloadConfig(**request['config'])
    _apply_worker_limits(config)
    if request.get('extract_to'):
        extract_pages(sys.argv[1], request['extract_to'], request['pages'])
        return
    result = inspect_pdf(sys.argv[1], request['paper'], config)
    print(json.dumps(asdict(result)))


if __name__ == '__main__':
    _worker()
