"""Semantic Scholar open-access PDFs and external IDs."""

from paper_author_searcher.config import DEFAULT_PDF, DEFAULT_SOURCES
from paper_author_searcher.http import SourceError
from paper_author_searcher.matching import normalize_title
from paper_author_searcher.pdf_identity import arxiv_ids, metadata_identity, paper_dois, paper_ids, source_records
from paper_author_searcher.pdf_source_requests import SemanticScholarRequests
from paper_author_searcher.status import ProviderStatus

from .base import MetadataPDFProvider, make_candidate


def semantic_candidates(paper, work, provider, metadata_url=None, config=DEFAULT_PDF):
    if not isinstance(work, dict):
        raise SourceError('Malformed Semantic Scholar paper: expected an object', error_type='malformed_response')
    location = work.get('openAccessPdf') or {}
    if not isinstance(location, dict):
        raise SourceError('Malformed Semantic Scholar openAccessPdf: expected an object')
    if not location.get('url') or str(location.get('status', '')).upper() == 'CLOSED':
        return []
    external_ids = work.get('externalIds') or {}
    authors = work.get('authors') or []
    if not isinstance(external_ids, dict) or not isinstance(authors, list):
        raise SourceError('Malformed Semantic Scholar paper identifiers or authors', error_type='malformed_response')
    if not all(isinstance(author, dict) for author in authors):
        raise SourceError('Malformed Semantic Scholar author list', error_type='malformed_response')
    identity = metadata_identity(
        paper,
        'semantic_scholar',
        work.get('paperId'),
        external_ids.get('DOI'),
        work.get('title'),
        work.get('year'),
        [author.get('name', '') for author in authors],
        [external_ids['ArXiv']] if external_ids.get('ArXiv') else (),
        config,
    )
    provenance = {
        'database': 'semantic_scholar',
        'source_paper_id': work.get('paperId'),
        'doi': external_ids.get('DOI'),
        'metadata_url': metadata_url,
        'metadata_title': work.get('title'),
        'metadata_year': work.get('year'),
        'metadata_authors': [author.get('name', '') for author in authors],
        'oa_status': location.get('status'),
        'license': location.get('license'),
    }
    return [
        make_candidate(
            location['url'],
            provider,
            identity,
            provenance,
            'Semantic Scholar openAccessPdf metadata',
            license=location.get('license'),
        )
    ]


class SemanticScholarPDFProvider(MetadataPDFProvider):
    name = 'semantic_scholar'
    fields = SemanticScholarRequests.fields

    def __init__(self, client, sources=DEFAULT_SOURCES, api_key=None, config=DEFAULT_PDF):
        super().__init__(client, sources)
        self.api_key = api_key
        self.config = config
        self.requests = SemanticScholarRequests(self.client, self.api_key, config, sources)

    @staticmethod
    def _saved_work(paper, identifier):
        for record in source_records(paper):
            work = record.get('raw_metadata') or {}
            if record.get('source') != 'semantic_scholar' or 'openAccessPdf' not in work:
                continue
            if work.get('paperId') == identifier:
                return work
        return None

    def prepare(self, papers):
        identifiers = []
        for paper in papers:
            for identifier in self.lookups(paper):
                if self._saved_work(paper, identifier) is None:
                    identifiers.append(identifier)
        self.requests.prefetch(identifiers)

    def request_statistics(self):
        return dict(
            self.requests.statistics,
            events=list(self.requests.events),
            scope='logical calls; HTTP retries are not included',
        )

    def find(self, paper):
        result = super().find(paper)
        for diagnostic in result.diagnostics:
            diagnostic.update(self.requests.diagnostics(diagnostic['identifier']))
        not_indexed = result.diagnostics and all(item.get('status_code') == 404 for item in result.diagnostics)
        if not self.lookups(paper) or not_indexed:
            self._search_title(paper, result)
        if result.errors and result.diagnostics and all(item.get('status_code') == 404 for item in result.diagnostics):
            result.status = ProviderStatus.NOT_FOUND
        return result

    def _search_title(self, paper, result):
        if not self.config.semantic_scholar_title_search or not paper.get('title'):
            return
        try:
            works = self.requests.search(paper['title'])
            for work in works:
                if normalize_title(work.get('title') or '') == normalize_title(paper['title']):
                    result.candidates.extend(
                        semantic_candidates(
                            paper, work, self.name, self.sources.semantic_scholar_url + '/paper/search', self.config
                        )
                    )
            failed = any(
                item.get('status') == ProviderStatus.ERROR and item.get('error_type') != 'not_found'
                for item in result.diagnostics
            )
            result.status = ProviderStatus.PARTIAL if failed else ProviderStatus.OK
            result.diagnostics.append({'lookup_method': 'title_search', 'status': ProviderStatus.OK})
        except (SourceError, ValueError, TypeError) as exc:
            if getattr(exc, 'error_type', None) == 'source_deferred':
                result.status = ProviderStatus.PARTIAL
                result.warnings.append({'code': 'source_deferred', 'incomplete': True, 'lookup_method': 'title_search'})
            elif getattr(exc, 'status_code', None) == 404:
                result.status = ProviderStatus.NOT_FOUND
            else:
                result.status = ProviderStatus.ERROR
                result.errors.append('Title search: ' + str(exc))
            result.diagnostics.append(
                {
                    'lookup_method': 'title_search',
                    'status': ProviderStatus.ERROR,
                    'status_code': getattr(exc, 'status_code', None),
                    'error_type': getattr(exc, 'error_type', 'malformed_response'),
                    'retry_after': getattr(exc, 'retry_after', None),
                    'request_context': getattr(exc, 'request_context', {}),
                }
            )

    def lookups(self, paper):
        identifiers = paper_ids(paper, self.name)
        if identifiers:
            return identifiers
        identifiers = ['DOI:' + doi[len('doi:') :] for doi in sorted(paper_dois(paper))]
        identifiers.extend('ARXIV:' + value for value in arxiv_ids(paper))
        return identifiers

    def lookup(self, paper, identifier):
        work = self._saved_work(paper, identifier)
        if work is not None:
            return semantic_candidates(paper, work, self.name, 'saved_semantic_scholar_metadata', self.config), []
        work = self.requests.work(identifier)
        return semantic_candidates(paper, work, self.name, self.requests.url(identifier), self.config), []
