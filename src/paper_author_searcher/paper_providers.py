"""Author-ID scoped metadata adapters; reuse resolver clients and configuration."""

import logging
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Protocol

from .config import DEFAULT_COLLECTION, CollectionConfig
from .dblp_format import arxiv_ids, base_name, plain_title
from .http import SourceError
from .logging_config import log_isolated_failure
from .matching import canonical_doi
from .models import PaperRecord, PaperSourceResult
from .paper_authorship import canonical_author_id, target_membership
from .providers import DBLPProvider, OpenAlexProvider, SemanticScholarProvider

logger = logging.getLogger(__name__)


class PaperProvider(Protocol):
    @property
    def name(self) -> str: ...

    def collect(self, author_id: str) -> PaperSourceResult: ...


def doi_value(value):
    if value:
        return canonical_doi(value)[len('doi:') :]
    return None


def text_content(element, path):
    node = element.find(path)
    if node is None:
        return None
    return ''.join(node.itertext()).strip() or None


def parse_year(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def reconstruct_abstract(index):
    if not index:
        return None
    words = {}
    for word, positions in index.items():
        for position in positions:
            words[position] = word
    return ' '.join(words[position] for position in sorted(words))


class PagedPaperProvider(ABC):
    """Template method for paginated sources: keep prior pages when a later page fails, detect stalled paging.

    Subclasses supply the source ``name``, the ``initial_token`` and the two hooks below.
    """

    name: ClassVar[str]
    initial_token: ClassVar[Any]
    config: CollectionConfig

    @abstractmethod
    def _fetch_page(self, author_id: str, token: Any) -> tuple[list[Any], Any]:
        """Return one page of raw records and the next token (``None`` when there are no more pages)."""

    @abstractmethod
    def _parse_paper(self, item: Any, author_id: str) -> PaperRecord:
        """Convert one raw record into a ``PaperRecord``; raise to reject it."""

    def collect(self, author_id):
        result = PaperSourceResult(source=self.name, author_id=author_id)
        token = self.initial_token
        visited = set()
        try:
            while token is not None:
                if token in visited:
                    raise SourceError('Pagination token repeated; results are incomplete')
                limit = self.config.maximum_pages
                if limit is not None and result.pages_fetched >= limit:
                    raise SourceError('Configured page limit reached; results are incomplete')
                visited.add(token)
                records, next_token = self._fetch_page(author_id, token)
                result.pages_fetched += 1
                self._add_records(result, records, author_id)
                if next_token is not None and not records:
                    raise SourceError('Empty page with continuation; results are incomplete')
                token = next_token
            result.pagination_complete = True
            result.complete = not result.rejected_records and not result.metadata_errors
        except Exception as exc:
            log_isolated_failure(logger, '%s paper collection failed for author %s: %s', self.name, author_id, exc)
            result.error = str(exc)
        return result

    def _add_records(self, result, records, author_id):
        for item in records:
            result.records_received += 1
            try:
                paper = self._parse_paper(item, author_id)
                if paper.target_author_present is False:
                    paper.attribution_evidence.append('Target Author ID absent from authorship metadata')
                elif paper.target_author_present is None:
                    paper.attribution_evidence.append('Returned authorship metadata cannot establish target presence')
                if not paper.title or not paper.source_paper_id:
                    paper.attribution_evidence.append('Missing title or paper ID')
                result.papers.append(paper)
                for error in paper.metadata_errors:
                    result.metadata_errors.append({'paper_id': paper.source_paper_id, 'error': error})
            except Exception as exc:
                result.rejected_records.append(
                    {
                        'paper_id': (item.get('id') or item.get('paperId')) if isinstance(item, dict) else None,
                        'reason': str(exc),
                        'raw_metadata': item,
                    }
                )


class OpenAlexPaperProvider(PagedPaperProvider):
    name = 'openalex'
    initial_token = '*'
    FIELDS = (
        'id,title,authorships,publication_year,doi,primary_location,locations,best_oa_location,open_access,'
        'abstract_inverted_index,type,ids,is_authors_truncated,primary_topic'
    )

    def __init__(self, provider: OpenAlexProvider, config=DEFAULT_COLLECTION):
        self.provider = provider
        self.config = config

    def _fetch_page(self, author_id, token):
        data = self.provider.client.get(
            self.provider.base_url + '/works',
            {
                'filter': 'authorships.author.id:' + author_id,
                'cursor': token,
                'per-page': self.config.openalex_page_size,
                'select': self.FIELDS,
                'api_key': self.provider.api_key,
            },
        )
        if not isinstance(data.get('results'), list) or 'next_cursor' not in data.get('meta', {}):
            raise SourceError('Malformed OpenAlex pagination response')
        return data['results'], data['meta']['next_cursor']

    def parse_work(self, item, author_id):
        """One OpenAlex work as a record of ``author_id`` (used for works fetched outside author paging)."""
        return self._parse_paper(item, author_id)

    def _parse_paper(self, item, author_id):
        item, errors = self._expand_authors(item)
        authors = []
        details = []
        for authorship in item.get('authorships') or []:
            author = authorship.get('author') or {}
            name = authorship.get('raw_author_name') or author.get('display_name')
            if name:
                authors.append(name)
            details.append(
                {
                    'name': author.get('display_name'),
                    'raw_author_name': authorship.get('raw_author_name'),
                    'author_id': author.get('id'),
                    'orcid': author.get('orcid'),
                    'orcid_scope': 'resolved_author',
                    'role': 'author',
                    'author_position': authorship.get('author_position'),
                    'is_corresponding': authorship.get('is_corresponding'),
                    'institutions': authorship.get('institutions') or [],
                    'affiliations': authorship.get('affiliations') or [],
                    'affiliation_scope': 'paper_authorship',
                    'raw_affiliation_strings': authorship.get('raw_affiliation_strings') or [],
                }
            )
        location = item.get('primary_location') or {}
        venue = (location.get('source') or {}).get('display_name')
        complete = None
        if isinstance(item.get('is_authors_truncated'), bool):
            complete = not item['is_authors_truncated']
        external_ids = {}
        for namespace, value in (item.get('ids') or {}).items():
            if value:
                external_ids[namespace.lower()] = str(value)
        if item.get('id'):
            external_ids.setdefault('openalex', item['id'])
        return PaperRecord(
            title=item.get('title') or '',
            authors=authors,
            year=parse_year(item.get('publication_year')),
            doi=doi_value(item.get('doi')),
            venue=venue,
            abstract=reconstruct_abstract(item.get('abstract_inverted_index')),
            source=self.name,
            source_paper_id=item.get('id') or '',
            source_author_id=author_id,
            paper_url=location.get('landing_page_url') or item.get('doi') or item.get('id'),
            publication_type=item.get('type'),
            author_details=details,
            authors_complete=complete,
            target_author_present=target_membership(self.name, author_id, details, complete),
            external_ids=external_ids,
            raw_metadata=item,
            metadata_errors=errors,
        )

    def _expand_authors(self, item):
        if not item.get('is_authors_truncated'):
            return item, []
        if not self.config.expand_truncated_openalex_authors:
            return item, ['OpenAlex authorship list truncated; expansion disabled']
        paper_id = item.get('id')
        if not paper_id:
            return item, ['Truncated authorship list has no work ID for expansion']
        try:
            expanded = self.provider.client.get(
                self.provider.base_url + '/works/' + paper_id.rsplit('/', 1)[-1],
                {'api_key': self.provider.api_key},
            )
            if expanded.get('id') != paper_id or not isinstance(expanded.get('authorships'), list):
                raise SourceError('Expanded work ID or authorship metadata mismatch')
            if expanded.get('is_authors_truncated'):
                raise SourceError('Individual work authorship list is still truncated')
            values = dict(item)
            values['authorships'] = expanded['authorships']
            values['is_authors_truncated'] = False
            # Preserve both responses, including the original list truncation indication.
            values['_authorship_expansion'] = expanded
            values['_list_response'] = item
            return values, []
        except Exception as exc:
            log_isolated_failure(logger, 'OpenAlex authorship expansion failed: %s', exc)
            return item, ['OpenAlex authorship expansion failed: ' + str(exc)]


class SemanticScholarPaperProvider(PagedPaperProvider):
    name = 'semantic_scholar'
    initial_token = 0
    FIELDS = 'title,authors,year,externalIds,venue,abstract,url,publicationTypes,openAccessPdf'
    AUTHOR_FIELDS = 'name,externalIds,affiliations'

    def __init__(self, provider: SemanticScholarProvider, config=DEFAULT_COLLECTION):
        self.provider = provider
        self.config = config

    def collect(self, author_id):
        result = super().collect(author_id)
        if not result.papers or not self.config.semantic_scholar_author_identity:
            return result
        try:
            metadata = self.provider.client.get(
                self.provider.base_url + '/author/' + author_id,
                {'fields': self.AUTHOR_FIELDS},
                self._headers(),
            )
            if str(metadata.get('authorId')) != str(author_id):
                raise SourceError('Author identity metadata ID mismatch')
            self._attach_identity(result.papers, author_id, metadata)
        except Exception as exc:
            log_isolated_failure(
                logger, 'Semantic Scholar identity enrichment failed for author %s: %s', author_id, exc
            )
            error = 'Semantic Scholar author identity enrichment failed: ' + str(exc)
            result.metadata_errors.append({'author_id': author_id, 'error': error})
            for paper in result.papers:
                paper.metadata_errors.append(error)
            result.complete = False
        return result

    def _headers(self):
        if self.provider.api_key:
            return {'x-api-key': self.provider.api_key}
        return {}

    def _attach_identity(self, papers, author_id, metadata):
        for paper in papers:
            for detail in paper.author_details:
                if canonical_author_id(self.name, detail.get('author_id')) != str(author_id):
                    continue
                # Profile affiliations are not evidence of this paper's printed institution.
                detail['author_profile'] = metadata
                detail['author_profile_url'] = self.provider.base_url + '/author/' + author_id
                if not detail.get('orcid') and not detail.get('orcids'):
                    values = self._orcids(metadata.get('externalIds') or {})
                    detail['orcids'] = values
                    detail['orcid'] = values[0] if len(values) == 1 else None
                    detail['orcid_scope'] = 'author_profile'
                detail['profile_affiliations'] = metadata.get('affiliations') or []

    @staticmethod
    def _orcids(external_ids):
        value = external_ids.get('ORCID') or external_ids.get('orcid')
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str) and item]
        return []

    def parse_work(self, item, author_id):
        """One Semantic Scholar paper as a record of ``author_id`` (used for papers fetched outside author paging)."""
        return self._parse_paper(item, author_id)

    def _fetch_page(self, author_id, token):
        data = self.provider.client.get(
            self.provider.base_url + '/author/' + author_id + '/papers',
            {'offset': token, 'limit': self.config.semantic_scholar_page_size, 'fields': self.FIELDS},
            self._headers(),
        )
        if not isinstance(data.get('data'), list) or data.get('offset') != token:
            raise SourceError('Malformed Semantic Scholar pagination response')
        next_token = data.get('next')
        if next_token is not None and (not isinstance(next_token, int) or next_token <= token):
            raise SourceError('Invalid Semantic Scholar continuation offset')
        return data['data'], next_token

    def _parse_paper(self, item, author_id):
        authors = []
        for author in item.get('authors') or []:
            if author.get('name'):
                authors.append(author['name'])
        details = []
        for author in item.get('authors') or []:
            external = author.get('externalIds') or {}
            orcids = self._orcids(external)
            details.append(
                {
                    'name': author.get('name'),
                    'author_id': author.get('authorId'),
                    'orcid': orcids[0] if len(orcids) == 1 else None,
                    'orcids': orcids,
                    'external_ids': external,
                    'orcid_scope': 'author_profile',
                    'profile_affiliations': author.get('affiliations') or [],
                    'role': 'author',
                }
            )
        external_ids = {}
        for key, value in (item.get('externalIds') or {}).items():
            if value is not None:
                external_ids[key.lower()] = str(value)
        types = item.get('publicationTypes') or []
        return PaperRecord(
            title=item.get('title') or '',
            authors=authors,
            year=parse_year(item.get('year')),
            doi=doi_value((item.get('externalIds') or {}).get('DOI')),
            venue=item.get('venue') or None,
            abstract=item.get('abstract'),
            source=self.name,
            source_paper_id=item.get('paperId') or '',
            source_author_id=author_id,
            paper_url=item.get('url'),
            publication_type=','.join(sorted(types)) or None,
            author_details=details,
            external_ids=external_ids,
            raw_metadata=item,
            target_author_present=target_membership(self.name, author_id, details),
        )


class DBLPPaperProvider:
    name = 'dblp'

    def __init__(self, provider: DBLPProvider):
        self.provider = provider

    def collect(self, author_id):
        result = PaperSourceResult(source=self.name, author_id=author_id)
        try:
            root = self.provider.client.get(
                self.provider.base_url + '/pid/' + author_id + '.xml',
                xml=True,
            )
            if root.tag != 'dblpperson' or root.get('pid') != author_id:
                result.rejected_records.append(
                    {
                        'paper_id': None,
                        'reason': 'Bibliography PID mismatch',
                        'raw_metadata': {'xml': ET.tostring(root, encoding='unicode')},
                    }
                )
                raise SourceError('DBLP bibliography PID is missing or does not match target')
            result.pages_fetched = 1
            for container in root.findall('r'):
                self._add_record(result, container, author_id)
            expected_count = root.get('n')
            if expected_count is not None and int(expected_count) != result.records_received:
                raise SourceError('DBLP bibliography record count mismatch')
            result.pagination_complete = True
            result.complete = not result.rejected_records
        except Exception as exc:
            log_isolated_failure(logger, '%s paper collection failed for author %s: %s', self.name, author_id, exc)
            result.error = str(exc)
        return result

    def _add_record(self, result, container, author_id):
        result.records_received += 1
        record = next(iter(container), None)
        if record is None:
            result.rejected_records.append(
                {
                    'paper_id': None,
                    'reason': 'Empty DBLP record',
                    'raw_metadata': {'xml': ET.tostring(container, encoding='unicode')},
                }
            )
            return
        result.papers.append(dblp_record(record, author_id, self.provider.base_url))


def dblp_record(record: ET.Element, author_id: str, base_url: str) -> PaperRecord:
    """One DBLP publication element (from a PID bibliography or the data dump) as a record of ``author_id``."""
    authors = []
    verified = False
    for author in record.findall('author'):
        if author.text:
            authors.append(base_name(author.text))
        if author.get('pid') == author_id:
            verified = True
    references = []
    doi = None
    for reference in record.findall('ee'):
        if reference.text:
            references.append(reference.text)
            if 'doi.org/' in reference.text:
                doi = doi_value(reference.text)
    title = plain_title(text_content(record, 'title') or '')
    paper_id = record.get('key')
    external_ids = {'dblp': paper_id or ''}
    arxiv = arxiv_ids(references)
    if arxiv:
        external_ids['arxiv'] = arxiv[0]
    venue = text_content(record, 'journal') or text_content(record, 'booktitle')
    editor_only = not record.findall('author') and bool(record.findall('editor'))
    return PaperRecord(
        title=title or '',
        authors=authors,
        year=parse_year(text_content(record, 'year')),
        doi=doi,
        venue=venue,
        abstract=None,
        source=DBLPPaperProvider.name,
        source_paper_id=paper_id or '',
        source_author_id=author_id,
        paper_url=references[0] if references else base_url + '/rec/' + (paper_id or ''),
        publication_type=record.tag,
        author_details=[
            {'name': base_name(author.text or ''), 'author_id': author.get('pid'), 'role': 'author'}
            for author in record.findall('author')
        ],
        external_ids=external_ids,
        raw_metadata={'xml': ET.tostring(record, encoding='unicode')},
        target_author_present=False if editor_only else True if verified else None,
        attribution_evidence=[] if verified else ['Target PID absent from author metadata'],
    )
