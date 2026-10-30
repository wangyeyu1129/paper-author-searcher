"""Bind public PDF metadata to a saved paper without guessing resource paths."""

import re
from urllib.parse import urlsplit

from .config import DEFAULT_PDF, PDFConfig
from .matching import ARXIV_DOI_PREFIX, arxiv_from_doi, canonical_arxiv, canonical_doi, normalize_title
from .paper_authorship import byline_forms
from .paper_deduplication import author_signatures_match
from .status import IdentityStatus, OwnershipStatus


def source_records(paper):
    records = paper.get('records') or [paper]
    return [record for record in records if record.get('ownership_status') != OwnershipStatus.REJECTED]


def native_id(source, value):
    value = str(value or '').strip()
    if source == 'openalex':
        return value.rstrip('/').rsplit('/', 1)[-1].upper()
    return value


def paper_ids(paper, source):
    identifiers = []
    for record in source_records(paper):
        if record.get('source') == source and record.get('source_paper_id'):
            identifiers.append(native_id(source, record['source_paper_id']))
        for key, value in (record.get('external_ids') or {}).items():
            if key.lower().replace('_', '') == source.replace('_', '') and value:
                identifiers.append(native_id(source, value))
    return list(dict.fromkeys(identifiers))


def paper_dois(paper):
    identifiers = set()
    for record in [paper, *source_records(paper)]:
        if record.get('doi'):
            identifiers.add(canonical_doi(record['doi']))
        for key, value in (record.get('external_ids') or {}).items():
            if key.lower() == 'doi' and value:
                identifiers.add(canonical_doi(str(value)))
    return identifiers


def metadata_identity(
    paper,
    source,
    identifier=None,
    doi=None,
    title=None,
    year=None,
    authors=None,
    editions=(),
    config: PDFConfig = DEFAULT_PDF,
):
    """Stable identifiers bind a location; conflicting metadata blocks it.

    One work has editions: its arXiv preprint and its journal or proceedings version carry different DOIs, and the
    title sometimes changes between them (CRISPR-GPT's preprint was renamed for Nature Biomedical Engineering). A
    source naming one of the paper's own arXiv editions (as its DOI, as its arXiv ID, or among ``editions``, the arXiv
    IDs it lists for the same record) is not in conflict over another DOI, and a changed title is accepted when
    enough authors agree.
    """
    expected_dois = paper_dois(paper)
    doi_match = bool(doi and canonical_doi(doi) in expected_dois)
    edition = _names_edition(paper, source, identifier, doi, editions)
    if doi and expected_dois and not doi_match and not edition:
        return IdentityStatus.CONFLICTING, ['Source DOI conflicts with saved paper DOI identifiers']
    title_match = bool(title and normalize_title(title) == normalize_title(paper.get('title', '')))
    shared = _shared_authors(paper, authors or [], config)
    if title and paper.get('title') and not title_match:
        if edition and shared:
            return IdentityStatus.VERIFIED, [
                "Source names the paper's own arXiv edition and shares its authors; the title changed between editions"
            ]
        return IdentityStatus.CONFLICTING, ['Source title conflicts with saved paper title']
    if doi_match:
        return IdentityStatus.VERIFIED, ['Exact DOI agreement with saved paper metadata']
    if edition and title_match:
        return IdentityStatus.VERIFIED, ["Source names the paper's own arXiv edition with the same normalized title"]
    if identifier and native_id(source, identifier) in paper_ids(paper, source):
        if title_match:
            return IdentityStatus.VERIFIED, ['Exact source paper ID and normalized title agreement']
        return IdentityStatus.UNVERIFIED, ['Source paper ID agrees, but corroborating title/DOI is absent']
    author_match = any(_authors_agree(expected, authors or []) for expected in _author_lists(paper))
    # A preprint is often dated a year before its venue edition: the same title and full byline still bind it.
    if title_match and _years_agree(year, paper.get('year'), config.identity_year_tolerance) and author_match:
        return IdentityStatus.VERIFIED, [
            'Exact normalized title, publication year (within tolerance) and author signature agreement'
        ]
    return IdentityStatus.UNVERIFIED, ['Insufficient metadata to associate URL with this paper']


def _names_edition(paper, source, identifier, doi, editions):
    own = {canonical_arxiv(value) for value in edition_arxiv_ids(paper)} - {None}
    if not own:
        return False
    named = {arxiv_from_doi(doi)} if doi else set()
    if source == 'arxiv' and identifier:
        named.add(canonical_arxiv(str(identifier)))
    named.update(canonical_arxiv(str(value)) for value in editions or ())
    return bool(own & (named - {None}))


def _shared_authors(paper, observed, config):
    """Whether some saved author list and the source share enough author signatures (all of a shorter list)."""
    if not observed:
        return False
    for expected in _author_lists(paper):
        if not expected:
            continue
        unused = list(observed)
        shared = 0
        for author in expected:
            index = next((index for index, other in enumerate(unused) if _signature_agrees(author, other)), None)
            if index is not None:
                shared += 1
                unused.pop(index)
        if shared >= min(config.identity_shared_authors, len(expected)):
            return True
    return False


def _years_agree(observed, expected, tolerance):
    try:
        return abs(int(str(observed)[:4]) - int(str(expected)[:4])) <= tolerance
    except (TypeError, ValueError):
        return False


def _author_lists(paper):
    yield paper.get('authors') or []
    for record in source_records(paper):
        if record.get('authors_complete') is not True:
            continue
        details = record.get('author_details') or []
        if not details or any(author.get('role', 'author') != 'author' for author in details):
            continue
        if not all(author.get('name') and author.get('author_id') for author in details):
            continue
        yield [author['name'] for author in details]


def _authors_agree(expected, observed):
    if not expected or len(expected) != len(observed):
        return False
    unused = list(observed)
    for author in expected:
        matched = None
        for index, candidate in enumerate(unused):
            if _signature_agrees(author, candidate):
                matched = index
                break
        if matched is None:
            return False
        unused.pop(matched)
    return True


def _signature_agrees(left, right):
    for left_form in byline_forms(left):
        for right_form in byline_forms(right):
            if author_signatures_match(left_form, right_form):
                return True
    return False


def arxiv_id(value):
    """Recognize supplied arXiv identifiers, including explicit version suffixes."""
    value = str(value or '').strip()
    if '://' in value:
        parsed = urlsplit(value)
        if parsed.hostname not in ('arxiv.org', 'www.arxiv.org', 'export.arxiv.org'):
            return None
        value = re.sub(r'^/(?:abs|pdf)/', '', parsed.path)
        value = re.sub(r'\.pdf$', '', value)
    value = re.sub(r'^arxiv:', '', value, flags=re.IGNORECASE)
    if re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-zA-Z][a-zA-Z.\-]+/\d{7})(?:v\d+)?', value):
        return value
    return None


def arxiv_ids(paper):
    identifiers = []
    for record in source_records(paper):
        for key, value in (record.get('external_ids') or {}).items():
            if key.lower() == 'arxiv' and arxiv_id(value):
                identifiers.append(arxiv_id(value))
        if arxiv_id(record.get('paper_url')):
            identifiers.append(arxiv_id(record['paper_url']))
        metadata = record.get('raw_metadata') or {}
        for location in metadata.get('locations') or []:
            if not isinstance(location, dict):
                continue
            for key in ('landing_page_url', 'pdf_url'):
                value = arxiv_id(location.get(key))
                if value:
                    identifiers.append(value)
    for doi in sorted(paper_dois(paper)):
        prefix = 'doi:' + ARXIV_DOI_PREFIX
        if doi.startswith(prefix):
            identifier = arxiv_id(doi[len(prefix) :])
            if identifier:
                identifiers.append(identifier)
    return list(dict.fromkeys(identifiers))


def related_dois(paper):
    """DOIs of the editions deduplication related to the paper (same normalized title, compatible authors)."""
    dois = []
    metadata = paper.get('paper_metadata') or {}
    for related in paper.get('related_versions') or metadata.get('related_versions') or []:
        for identifier in (related or {}).get('identifiers') or []:
            if identifier.get('doi'):
                dois.append(canonical_doi(identifier['doi'])[len('doi:') :])
    return list(dict.fromkeys(dois))


def edition_arxiv_ids(paper):
    """arXiv identifiers of the paper and of the editions deduplication related to it (same normalized title and
    compatible authors). A PDF printing one of them is an edition of this work only if its title also matches."""
    identifiers = list(arxiv_ids(paper))
    metadata = paper.get('paper_metadata') or {}
    for related in paper.get('related_versions') or metadata.get('related_versions') or []:
        for identifier in (related or {}).get('identifiers') or []:
            if identifier.get('source') == 'arxiv' and arxiv_id(identifier.get('paper_id')):
                identifiers.append(arxiv_id(identifier['paper_id']))
            doi = canonical_doi(identifier.get('doi') or '')
            if doi.startswith('doi:' + ARXIV_DOI_PREFIX) and arxiv_id(doi[len('doi:' + ARXIV_DOI_PREFIX) :]):
                identifiers.append(arxiv_id(doi[len('doi:' + ARXIV_DOI_PREFIX) :]))
    return list(dict.fromkeys(identifiers))


def same_arxiv_version(requested, returned):
    if re.search(r'v\d+$', requested):
        return requested == returned
    return requested == re.sub(r'v\d+$', '', returned)
