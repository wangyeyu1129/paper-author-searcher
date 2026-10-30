"""Citation metadata (citation_pdf_url) on publisher landing pages."""

from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.html_support import decode_response
from paper_author_searcher.http import SourceError
from paper_author_searcher.models import PDFProviderResult
from paper_author_searcher.pdf_identity import metadata_identity, source_records
from paper_author_searcher.status import IdentityStatus, ProviderStatus

from .base import make_candidate
from .openalex import openalex_locations


class CitationMetadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower() != 'meta':
            return
        attributes = dict(attrs)
        name = (attributes.get('name') or attributes.get('property') or '').lower()
        content = attributes.get('content')
        if name.startswith('citation_') and content:
            self.values.setdefault(name, []).append(content)
        aliases = {
            'dc.title': 'citation_title',
            'dc.creator': 'citation_author',
            'dc.date': 'citation_date',
            'dc.date.issued': 'citation_date',
            'prism.doi': 'citation_doi',
            'eprints.document_url': 'citation_pdf_url',
        }
        if name in aliases and content:
            self.values.setdefault(aliases[name], []).append(content)


class LandingPagePDFProvider:
    """Read explicit scholarly citation_pdf_url, never arbitrary page hyperlinks."""

    name = 'landing_page'

    def __init__(self, client, config=DEFAULT_PDF):
        self.client = client
        self.config = config

    def find(self, paper):
        result = PDFProviderResult(self.name)
        pages = self._pages(paper)
        urls = list(pages)
        if not urls:
            result.status = ProviderStatus.NOT_APPLICABLE
            return result
        for url in urls:
            try:
                result.candidates.extend(self._lookup(paper, url, pages[url]))
            except (SourceError, ValueError, TypeError) as exc:
                result.errors.append(f'{url}: {exc}')
        if result.errors:
            result.status = ProviderStatus.PARTIAL if len(result.errors) < len(urls) else ProviderStatus.ERROR
        return result

    def _pages(self, paper):
        pages: dict[str, Any] = {}
        for record in source_records(paper):
            if record.get('paper_url'):
                pages.setdefault(record['paper_url'], [])
            metadata = record.get('raw_metadata') or {}
            for location in openalex_locations(metadata):
                if location.get('landing_page_url'):
                    pages.setdefault(location['landing_page_url'], [])
        for location in paper.get('_pdf_landing_pages', []):
            if location.get('landing_page_url'):
                pages.setdefault(location['landing_page_url'], []).append(location)
        return pages

    def _lookup(self, paper, url, bindings):
        response = self.client.probe(
            url, self.config.preview_bytes, self.config.html_metadata_bytes, self.config.maximum_redirects
        )
        return self._response_candidates(paper, response, bindings)

    def _response_candidates(self, paper, response, bindings):
        if not 200 <= response.status < 300:
            raise SourceError(f'Landing page HTTP {response.status}')
        if response.content_type not in ('text/html', 'application/xhtml+xml'):
            if response.prefix.lstrip().startswith(b'%PDF-'):
                return self._direct_pdf(paper, response, bindings)
            return []
        parser = CitationMetadata()
        parser.feed(decode_response(response))
        values = parser.values
        pdf_urls = values.get('citation_pdf_url', [])
        if not pdf_urls:
            return []
        title = self._single(values, 'citation_title')
        doi = self._single(values, 'citation_doi')
        date = self._single(values, 'citation_publication_date') or self._single(values, 'citation_date')
        year = date[:4] if date and date[:4].isdigit() else None
        identity = metadata_identity(
            paper, self.name, doi=doi, title=title, year=year, authors=values.get('citation_author'), config=self.config
        )
        for key in ('citation_title', 'citation_doi'):
            if len(set(values.get(key, []))) > 1:
                identity = (IdentityStatus.UNVERIFIED, ['Landing page contains multiple paper identities'])
        candidates = []
        for pdf_url in pdf_urls:
            candidates.append(
                make_candidate(
                    urljoin(response.url, pdf_url),
                    self.name,
                    identity,
                    {
                        'metadata_url': response.url,
                        'doi': doi,
                        'title': title,
                        'metadata_year': year,
                        'metadata_authors': values.get('citation_author', []),
                        'metadata_bytes_read': len(response.prefix),
                    },
                    'Public scholarly page explicitly supplies citation_pdf_url',
                )
            )
        return candidates

    def _direct_pdf(self, paper, response, bindings):
        candidates = []
        for binding in bindings:
            if binding.get('is_oa') is not True:
                continue
            identity = metadata_identity(
                paper,
                binding['database'],
                binding.get('source_paper_id'),
                binding.get('doi'),
                binding.get('title'),
                config=self.config,
            )
            candidates.append(
                make_candidate(
                    response.url,
                    self.name,
                    identity,
                    dict(binding, metadata_url=binding['landing_page_url']),
                    'OpenAlex OA landing location returned an actual PDF signature',
                    binding.get('version'),
                    binding.get('license'),
                )
            )
        return candidates

    def _single(self, values, key):
        items = values.get(key) or []
        return items[0] if len(set(items)) == 1 else None
