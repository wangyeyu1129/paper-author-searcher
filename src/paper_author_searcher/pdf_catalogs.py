"""Discover explicit article pages in configurable scholarly journal catalogs."""

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from .config import DEFAULT_PDF
from .html_support import LinkExtractor, decode_response
from .http import SourceError
from .matching import normalize, normalize_title
from .models import PDFProviderResult
from .pdf_identity import source_records
from .pdf_providers import LandingPagePDFProvider, openalex_locations
from .status import ProviderStatus


class JournalCatalogPDFProvider(LandingPagePDFProvider):
    """Catalog title selects a landing page; its citation metadata binds the PDF."""

    name = 'journal_catalog'

    def __init__(self, client, config=DEFAULT_PDF):
        super().__init__(client, config)
        self.cache = {}

    def find(self, paper):
        venues = self._venues(paper)
        catalogs = [url for venue, url in self.config.journal_catalogs if normalize(venue) in venues]
        if not catalogs or paper.get('year') is None:
            return PDFProviderResult(self.name, status=ProviderStatus.NOT_APPLICABLE)
        result = PDFProviderResult(self.name)
        for catalog in catalogs:
            try:
                volumes = self._volumes(catalog, paper['year'])
                for volume in volumes:
                    for link in self._links(volume):
                        title = link['heading'] or link['title']
                        if normalize_title(title) != normalize_title(paper.get('title') or ''):
                            continue
                        page = self._catalog_url(volume, link['url'])
                        if page is None or not urlsplit(page).path.endswith(('.html', '.htm')):
                            continue
                        candidates = self._lookup(paper, page, [])
                        for candidate in candidates:
                            candidate.provenance[0]['catalog_url'] = catalog
                            candidate.provenance[0]['volume_url'] = volume
                        result.candidates.extend(candidates)
            except (SourceError, ValueError, TypeError) as exc:
                result.errors.append(f'{catalog}: {exc}')
        if result.errors:
            result.status = ProviderStatus.PARTIAL if result.candidates else ProviderStatus.ERROR
        return result

    @staticmethod
    def _venues(paper):
        venues = set()
        for record in source_records(paper):
            if record.get('venue'):
                venues.add(normalize(record['venue']))
            for location in openalex_locations(record.get('raw_metadata') or {}):
                source = location.get('source') or {}
                if isinstance(source, dict) and source.get('display_name'):
                    venues.add(normalize(source['display_name']))
        return venues

    def _volumes(self, catalog, year):
        volumes = []
        for link in self._links(catalog):
            years = [int(value) for value in re.findall(r'\b(?:19|20)\d{2}\b', link['context'])]
            if not years or not min(years) <= year <= max(years):
                continue
            url = self._catalog_url(catalog, link['url'])
            if url and url not in volumes:
                volumes.append(url)
        return volumes

    def _links(self, url):
        if url in self.cache:
            saved = self.cache[url]
            if isinstance(saved, Exception):
                raise saved
            return saved
        try:
            response = self.client.probe(
                url, self.config.preview_bytes, self.config.catalog_preview_bytes, self.config.maximum_redirects
            )
            if response.content_type not in ('text/html', 'application/xhtml+xml'):
                raise SourceError('Catalog did not return HTML')
            if len(response.prefix) >= self.config.catalog_preview_bytes:
                raise SourceError('Catalog exceeds preview limit; lookup is incomplete')
            links = catalog_links(decode_response(response))
            for link in links:
                # Directory redirects can add a trailing slash before relative links.
                link['url'] = urljoin(response.url, link['url'])
            self.cache[url] = links
            return links
        except (SourceError, ValueError, TypeError) as exc:
            self.cache[url] = exc
            raise

    @staticmethod
    def _catalog_url(base, link):
        target = urljoin(base, link)
        if urlsplit(target).hostname != urlsplit(base).hostname:
            return None
        if urlsplit(target).scheme not in ('http', 'https'):
            return None
        return target


CATALOG_CONTEXT_CHARACTERS = 180


def catalog_links(html: str) -> list[dict[str, Any]]:
    """Article links of a journal catalog: title, the dt heading and the byline text that follows each link."""
    parser = LinkExtractor(heading_tags=('dt',), body_tags=('dd',), trailing_context=CATALOG_CONTEXT_CHARACTERS)
    parser.feed(html)
    return [
        {'url': link['href'], 'title': link['text'], 'context': link['context'], 'heading': link['heading']}
        for link in parser.links
    ]
