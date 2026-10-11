"""Follow explicit scholarly page links without inventing paths or byline evidence."""
import json
from collections import OrderedDict
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from .http import SourceError, require_public_http_url, has_pdf_signature
from .matching import normalize
from .models import PDFProviderResult
from .pdf_identity import metadata_identity
from .pdf_providers import CitationMetadata, LandingPagePDFProvider, make_candidate


class PublicLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None
        self.heading = ''
        self.in_heading = False
        self.scripts = []
        self.script = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attributes = dict(attrs)
        if tag in ('dt', 'h2', 'h3'):
            self.heading = ''
            self.in_heading = True
        elif tag == 'dd':
            self.in_heading = False
        if tag == 'script' and attributes.get('type') == 'application/ld+json':
            self.script = ''
        if tag in ('a', 'link') and attributes.get('href'):
            item = dict(attributes, text='', heading=self.heading)
            if tag == 'link':
                self.links.append(item)
            else:
                self.current = item

    def handle_data(self, value):
        if self.script is not None:
            self.script += value
        elif self.in_heading:
            self.heading += value
        if self.current is not None:
            self.current['text'] += value

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ('dt', 'h2', 'h3'):
            self.in_heading = False
        if tag == 'a' and self.current is not None:
            self.links.append(self.current)
            self.current = None
        if tag == 'script' and self.script is not None:
            self.scripts.append(self.script)
            self.script = None


def secure_page_url(url):
    """Prefer TLS for a supplied HTTP path; do not guess any new resource path."""
    require_public_http_url(url)
    parsed = urlsplit(url)
    if parsed.scheme == 'http' and parsed.port is None:
        return urlunsplit(('https', parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    return url


class PublicPagePDFProvider(LandingPagePDFProvider):
    name = 'public_pages'

    def __init__(self, client=None, config=None):
        if config is None:
            super().__init__(client)
        else:
            super().__init__(client, config)
        self.cache = OrderedDict()

    def find(self, paper):
        pages = self._pages(paper)
        for url in self.config.bibliography_urls:
            pages.setdefault(url, [])
        if not pages:
            return PDFProviderResult(self.name, status='not_applicable')
        result = PDFProviderResult(self.name)
        visited = set()
        for url, bindings in pages.items():
            try:
                actual = secure_page_url(url)
                if actual in visited:
                    continue
                visited.add(actual)
                response = self._probe(actual)
                result.candidates.extend(self._response_candidates(paper, response, bindings))
                if response.content_type in ('text/html', 'application/xhtml+xml'):
                    self._linked_candidates(paper, response, result, visited)
            except (SourceError, ValueError, TypeError) as exc:
                result.errors.append('{}: {}'.format(url, exc))
        if result.errors:
            result.status = 'partial' if result.candidates else 'error'
        return result

    def _probe(self, url):
        if url in self.cache:
            self.cache.move_to_end(url)
            response = self.cache[url]
            if isinstance(response, Exception):
                raise response
            return response
        try:
            response = self.client.probe(url, self.config.preview_bytes,
                                         self.config.html_metadata_bytes, self.config.maximum_redirects)
        except (SourceError, ValueError, TypeError) as exc:
            response = exc
        self.cache[url] = response
        if len(self.cache) > self.config.public_page_cache_entries:
            self.cache.popitem(last=False)
        if isinstance(response, Exception):
            raise response
        return response

    def _linked_candidates(self, paper, response, result, visited):
        content = response.prefix.decode('utf-8', errors='replace')
        parser = PublicLinks()
        parser.feed(content)
        if len(response.prefix) >= self.config.html_metadata_bytes:
            result.errors.append('Public page metadata preview limit reached; parsing may be incomplete')
        citation = CitationMetadata()
        citation.feed(content)
        values = citation.values
        date = self._single(values, 'citation_publication_date') or self._single(values, 'citation_date')
        identity = metadata_identity(paper, self.name, doi=self._single(values, 'citation_doi'),
                                     title=self._single(values, 'citation_title'), year=date[:4] if date else None,
                                     authors=values.get('citation_author'))
        for link in parser.links:
            alternate_pdf = link.get('type') == 'application/pdf' and 'alternate' in link.get('rel', '').split()
            title_matches = normalize(link['text'] or link['heading']) == normalize(paper.get('title') or '')
            if not alternate_pdf and not title_matches:
                continue
            try:
                target = secure_page_url(urljoin(response.url, link['href']))
            except (SourceError, ValueError) as exc:
                result.errors.append('Unsafe linked resource was not queried: ' + str(exc))
                continue
            if alternate_pdf:
                result.candidates.append(make_candidate(
                    target, self.name, identity, {'metadata_url': response.url,
                                                 'title': self._single(values, 'citation_title')},
                    'Scholarly page explicitly declares an alternate application/pdf resource'))
                continue
            if target in visited:
                continue
            if len(visited) >= len(self._pages(paper)) + len(self.config.bibliography_urls) + self.config.linked_page_limit:
                result.errors.append('Linked-page discovery limit reached; remaining links were not queried')
                break
            visited.add(target)
            try:
                child = self._probe(target)
                found = self._response_candidates(paper, child, [])
                if not found and has_pdf_signature(child.prefix):
                    found.append(make_candidate(
                        child.url, self.name, ('unverified', ['Bibliography link title alone cannot establish PDF identity']),
                        {'metadata_url': response.url, 'link_text': link['text']},
                        'Explicit public bibliography link returned a PDF; correspondence requires confirmation'))
                result.candidates.extend(found)
            except (SourceError, ValueError, TypeError) as exc:
                result.errors.append('{}: {}'.format(target, exc))
        for script in parser.scripts:
            result.candidates.extend(self._structured_candidates(paper, response.url, script))

    def _direct_pdf(self, paper, response, bindings):
        candidates = super()._direct_pdf(paper, response, bindings)
        configured_hosts = {urlsplit(url).hostname for url in self.config.bibliography_urls}
        hostname = urlsplit(response.url).hostname or ''
        labels = hostname.split('.')
        country_academic = len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in ('ac', 'edu')
        institutional = hostname.endswith('.edu') or country_academic
        for binding in bindings:
            if binding.get('is_oa') is True:
                continue
            source = binding.get('source') or {}
            declared_scholarly = isinstance(source, dict) and source.get('type') in ('journal', 'repository')
            if not institutional and hostname not in configured_hosts and not declared_scholarly:
                continue
            identity = metadata_identity(paper, binding.get('database'), binding.get('source_paper_id'),
                                         binding.get('doi'), binding.get('title'))
            candidates.append(make_candidate(
                response.url, self.name, identity,
                dict(binding, metadata_url=binding.get('landing_page_url'),
                     anonymous_pdf_response=True, indexed_is_oa=binding.get('is_oa')),
                'Supplied scholarly/institutional publication link returned an anonymous public PDF; license is unknown',
                binding.get('version'), binding.get('license')))
        return candidates

    def _structured_candidates(self, paper, url, script):
        try:
            data = json.loads(script)
        except ValueError:
            return []
        objects = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and isinstance(data.get('@graph'), list):
            objects = data['@graph']
        candidates = []
        for item in objects:
            if not isinstance(item, dict) or item.get('@type') != 'ScholarlyArticle':
                continue
            authors = item.get('author') or []
            if isinstance(authors, dict):
                authors = [authors]
            names = [author.get('name') for author in authors if isinstance(author, dict) and author.get('name')]
            date = item.get('datePublished') or ''
            identity = metadata_identity(paper, self.name, title=item.get('headline') or item.get('name'),
                                         year=str(date)[:4], authors=names)
            media = item.get('encoding') or item.get('associatedMedia') or []
            if isinstance(media, dict):
                media = [media]
            for document in media:
                if not isinstance(document, dict) or document.get('encodingFormat') != 'application/pdf':
                    continue
                if document.get('contentUrl'):
                    candidates.append(make_candidate(
                        urljoin(url, document['contentUrl']), self.name, identity,
                        {'metadata_url': url, 'title': item.get('headline') or item.get('name'),
                         'metadata_year': str(date)[:4], 'metadata_authors': names},
                        'ScholarlyArticle structured metadata explicitly declares a PDF encoding'))
        return candidates
