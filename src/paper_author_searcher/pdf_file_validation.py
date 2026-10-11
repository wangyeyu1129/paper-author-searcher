"""Parse PDFs in a bounded worker; distinguish file structure from paper identity."""
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import asdict

from .config import DEFAULT_DOWNLOAD, DownloadConfig
from .http import has_pdf_signature
from .matching import canonical_doi, normalize
from .models import PDFFileValidation
from .paper_deduplication import author_signatures_match
from .pdf_identity import metadata_identity


def validation_target(paper, candidate=None):
    """Preserve publisher bylines only after the URL's citation metadata was bound."""
    target = {key: paper.get(key) for key in ('title', 'authors', 'year', 'doi')}
    variants = []
    if candidate and candidate.get('identity_status') == 'verified' and candidate.get('validation_status') == 'verified':
        for provenance in candidate.get('provenance') or []:
            authors = provenance.get('metadata_authors') or []
            title = provenance.get('title')
            year = provenance.get('metadata_year')
            if not authors or not target['year'] or str(year) != str(target['year']):
                continue
            if normalize(title or '') != normalize(target['title'] or ''):
                continue
            identity, _ = metadata_identity(paper, 'landing_page', doi=provenance.get('doi'),
                                            title=title, year=year, authors=authors)
            signature_identity, _ = metadata_identity(paper, 'landing_page', title=title, year=year, authors=authors)
            if identity == 'verified' and signature_identity == 'verified' and authors not in variants:
                variants.append(authors)
    target['publisher_author_variants'] = variants
    return target


def content_identity(paper, titles, title_regions, text, author_metadata, config=DEFAULT_DOWNLOAD):
    expected_title = normalize(paper.get('title') or '')
    metadata_match = any(normalize(title) == expected_title for title in titles)
    title_match = metadata_match
    for title in title_regions:
        if expected_title and ' ' + expected_title + ' ' in ' ' + normalize(title) + ' ':
            title_match = True
            break
    observed_dois = set()
    for value in re.findall(r'10\.\d{4,9}/[^\s<>"\']+', text, flags=re.IGNORECASE):
        observed_dois.add(canonical_doi(_clean_extracted_doi(value))[len('doi:'):])
    expected_doi = canonical_doi(paper['doi'])[len('doi:'):] if paper.get('doi') else None
    doi_match = bool(expected_doi and expected_doi in observed_dois)
    title_conflict = bool(titles and not metadata_match)
    evidence = []
    if title_match:
        evidence.append('Expected normalized title occurs in PDF title metadata or the first-page title region')
    if doi_match:
        evidence.append('Target DOI found in the downloaded PDF front matter/metadata')
    if title_conflict:
        evidence.append('PDF title metadata differs from the target; content association remains conservative')
    if title_match and doi_match and not title_conflict:
        return 'verified', sorted(observed_dois), evidence
    authors_match = _authors_present(paper.get('authors') or [], title_regions, author_metadata)
    publisher_match = False
    if not authors_match:
        for authors in paper.get('publisher_author_variants') or []:
            if _authors_present(authors, title_regions, author_metadata):
                authors_match = True
                publisher_match = True
                break
    long_title = len(expected_title.split()) >= config.minimum_title_words_without_doi
    conflicting_doi = bool(expected_doi and observed_dois and not doi_match)
    if title_match and long_title and authors_match and not conflicting_doi and not title_conflict:
        evidence.append('Full expected author signatures corroborate a distinctive title without requiring DOI')
        if publisher_match:
            evidence.append('All bylines from the independently bound publisher citation metadata occur in PDF front matter')
        return 'verified', sorted(observed_dois), evidence
    if conflicting_doi:
        evidence.append('Front-matter DOI differs; this can also reflect a different version')
        if titles and not title_match:
            evidence.append('Explicit PDF title also differs from the target paper')
            return 'rejected', sorted(observed_dois), evidence
    evidence.append('File is parseable, but its correspondence to the target paper requires review')
    return 'needs_review', sorted(observed_dois), evidence


def _clean_extracted_doi(value):
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
    if not expected_authors:
        return False
    front_matter = normalize(' '.join(title_regions + [author_metadata]))
    names = re.split(r'[;,\n]|\s+and\s+', author_metadata)
    for author in expected_authors:
        normalized = normalize(author)
        if len(normalized.split()) < 2:
            return False
        if ' ' + normalized + ' ' in ' ' + front_matter + ' ':
            continue
        if any(author_signatures_match(author, name.strip()) for name in names if name.strip()):
            continue
        return False
    return True


def inspect_pdf(path, paper, config=DEFAULT_DOWNLOAD):
    from pypdf import PdfReader

    result = PDFFileValidation(identity_status='not_checked')
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
            ending = re.search(br'startxref\s+(\d+)\s+%%EOF\s*$', footer)
            if not ending or not 0 < int(ending.group(1)) < size:
                raise ValueError('PDF cross-reference/EOF structure is absent or truncated')
            handle.seek(0)
            reader = PdfReader(handle, strict=True)
            if reader.is_encrypted:
                raise ValueError('Encrypted PDFs are not decrypted or bypassed')
            result.page_count = len(reader.pages)
            if not 0 < result.page_count <= config.maximum_pages:
                raise ValueError('PDF page count is outside the configured limit')
            metadata = reader.metadata or {}
            title = str(metadata.get('/Title') or '')[:config.maximum_extracted_characters]
            author = str(metadata.get('/Author') or '')[:config.maximum_extracted_characters]
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
                    text = (page.extract_text() or '')[:config.maximum_extracted_characters]
                    text_parts.append(text)
                    title_regions.append(' '.join(text.splitlines()[:config.title_lines]))
                except Exception as exc:
                    result.error_details.append('Text extraction unavailable: ' + str(exc)[:1000])
            root = reader.trailer['/Root']
            names = root.get('/Names')
            result.active_content_present = bool(root.get('/OpenAction') or root.get('/AA'))
            if names is not None and '/JavaScript' in names.get_object():
                result.active_content_present = True
            metadata_text = ' '.join(str(metadata.get(key) or '') for key in ('/Title', '/Subject', '/Keywords', '/DOI'))
            text = '\n'.join(text_parts + [metadata_text])
            identity, dois, evidence = content_identity(paper, result.extracted_titles, title_regions, text, author, config)
            result.format_status = 'valid'
            result.identity_status = identity
            result.validation_status = identity
            result.extracted_dois = dois
            result.evidence = ['Strict PDF parser resolved pages and page content streams; EOF and startxref checked'] + evidence
    except Exception as exc:
        result.format_status = 'invalid'
        result.validation_status = 'invalid'
        result.error_details.append(str(exc)[:1000])
    return result


class PDFFileValidator:
    def __init__(self, config=DEFAULT_DOWNLOAD):
        self.config = config
        try:
            import pypdf
        except ImportError as exc:
            raise RuntimeError('Install the download extra: pip install -e ".[download]"') from exc

    def validate(self, path, paper):
        try:
            import pypdf  # Dependency is required only by the download stage.
        except ImportError as exc:
            raise RuntimeError('Install the download extra: pip install -e ".[download]"') from exc
        target = {key: paper.get(key) for key in ('title', 'authors', 'year', 'doi', 'publisher_author_variants')}
        request = json.dumps({'paper': target, 'config': asdict(self.config)})
        try:
            completed = subprocess.run(
                [sys.executable, '-m', 'paper_author_searcher.pdf_file_validation', str(path)],
                input=request, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=self.config.parser_timeout, check=False,
            )
            if completed.returncode != 0:
                raise ValueError('PDF parser worker failed or exceeded its resource limit')
            return PDFFileValidation(**json.loads(completed.stdout))
        except (subprocess.TimeoutExpired, OSError, ValueError, TypeError) as exc:
            return PDFFileValidation(identity_status='not_checked', validation_status='unverified',
                                     error_details=[str(exc)[:1000]])


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
    result = inspect_pdf(sys.argv[1], request['paper'], config)
    print(json.dumps(asdict(result)))


if __name__ == '__main__':
    _worker()
