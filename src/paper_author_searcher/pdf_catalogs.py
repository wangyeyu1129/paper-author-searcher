"""Discover explicit article pages in configurable scholarly journal catalogs."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .config import DEFAULT_PDF
from .http import HttpClient, SourceError
from .matching import normalize
from .models import PDFProviderResult
from .pdf_identity import source_records
from .pdf_providers import LandingPagePDFProvider, openalex_locations


class CatalogLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None
        self.trailing = None
        self.heading = ''
        self.in_heading = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == 'dt':
            self.heading = ''
            self.in_heading = True
        elif tag == 'dd':
            # HTML permits omitting </dt>; a definition body starts the byline.
            self.in_heading = False
        if tag == 'a':
            self.trailing = None
            self.current = {'url': dict(attrs).get('href'), 'title': '', 'context': '', 'heading': self.heading}

    def handle_data(self, text):
        if self.in_heading:
            self.heading += text
        if self.current is not None:
            self.current['title'] += text
        elif self.trailing is not None and len(self.trailing['context']) < 180:
            self.trailing['context'] += text

    def handle_endtag(self, tag):
        if tag.lower() == 'dt':
            self.in_heading = False
        if tag.lower() != 'a' or self.current is None:
            return
        if self.current['url']:
            self.links.append(self.current)
            self.trailing = self.current
        self.current = None


class JournalCatalogPDFProvider(LandingPagePDFProvider):
    """Catalog title selects a landing page; its citation metadata binds the PDF."""
    name = 'journal_catalog'

    def __init__(self, client=None, config=DEFAULT_PDF):
        super().__init__(client or HttpClient(), config)
        self.cache = {}

    def find(self, paper):
        venues = self._venues(paper)
        catalogs = [url for venue, url in self.config.journal_catalogs if normalize(venue) in venues]
        if not catalogs or paper.get('year') is None:
            return PDFProviderResult(self.name, status='not_applicable')
        result = PDFProviderResult(self.name)
        for catalog in catalogs:
            try:
                volumes = self._volumes(catalog, paper['year'])
                for volume in volumes:
                    for link in self._links(volume):
                        title = link['heading'] or link['title']
                        if normalize(title) != normalize(paper.get('title') or ''):
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
                result.errors.append('{}: {}'.format(catalog, exc))
        if result.errors:
            result.status = 'partial' if result.candidates else 'error'
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
            response = self.client.probe(url, self.config.preview_bytes,
                                         self.config.catalog_preview_bytes, self.config.maximum_redirects)
            if response.content_type not in ('text/html', 'application/xhtml+xml'):
                raise SourceError('Catalog did not return HTML')
            if len(response.prefix) >= self.config.catalog_preview_bytes:
                raise SourceError('Catalog exceeds preview limit; lookup is incomplete')
            parser = CatalogLinks()
            parser.feed(response.prefix.decode('utf-8', errors='replace'))
            for link in parser.links:
                # Directory redirects can add a trailing slash before relative links.
                link['url'] = urljoin(response.url, link['url'])
            self.cache[url] = parser.links
            return parser.links
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
