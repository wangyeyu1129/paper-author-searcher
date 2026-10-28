"""Bind public PDF metadata to a saved paper without guessing resource paths."""

import re
from urllib.parse import urlsplit

from .matching import ARXIV_DOI_PREFIX, canonical_doi, normalize
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


def metadata_identity(paper, source, identifier=None, doi=None, title=None, year=None, authors=None):
    """Stable identifiers bind a location; conflicting metadata blocks it."""
    expected_dois = paper_dois(paper)
    doi_match = bool(doi and canonical_doi(doi) in expected_dois)
    if doi and expected_dois and not doi_match:
        return IdentityStatus.CONFLICTING, ['Source DOI conflicts with saved paper DOI identifiers']
    title_match = bool(title and normalize(title) == normalize(paper.get('title', '')))
    if title and paper.get('title') and not title_match:
        return IdentityStatus.CONFLICTING, ['Source title conflicts with saved paper title']
    if doi_match:
        return IdentityStatus.VERIFIED, ['Exact DOI agreement with saved paper metadata']
    if identifier and native_id(source, identifier) in paper_ids(paper, source):
        if title_match:
            return IdentityStatus.VERIFIED, ['Exact source paper ID and normalized title agreement']
        return IdentityStatus.UNVERIFIED, ['Source paper ID agrees, but corroborating title/DOI is absent']
    author_match = False
    for expected_authors in _author_lists(paper):
        if _authors_agree(expected_authors, authors or []):
            author_match = True
            break
    if title_match and year and str(year) == str(paper.get('year')) and author_match:
        return IdentityStatus.VERIFIED, ['Exact normalized title, publication year and author signature agreement']
    return IdentityStatus.UNVERIFIED, ['Insufficient metadata to associate URL with this paper']


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
