"""Follow explicit scholarly page links without inventing paths or byline evidence."""

import json
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from .config import DEFAULT_PDF
from .http import SourceError, has_pdf_signature, require_public_http_url
from .matching import normalize
from .models import PDFProviderResult
from .pdf_identity import metadata_identity
from .pdf_providers import CitationMetadata, LandingPagePDFProvider, make_candidate
from .pdf_resource_requests import PublicResourceRequests
from .status import IdentityStatus, ProviderStatus, RequestOutcome


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

    def __init__(self, client, config=DEFAULT_PDF):
        super().__init__(client, config)
        if not isinstance(self.client, PublicResourceRequests):
            self.client = PublicResourceRequests(self.client, self.config)

    def find(self, paper):
        pages = self._pages(paper)
        for url in self.config.bibliography_urls:
            pages.setdefault(url, [])
        if not pages:
            return PDFProviderResult(self.name, status=ProviderStatus.NOT_APPLICABLE)
        result = PDFProviderResult(self.name)
        visited = set()
        linked_budget = {'requests': 0}
        for url, bindings in pages.items():
            try:
                actual = secure_page_url(url)
                if actual in visited:
                    continue
                visited.add(actual)
                response = self._probe(actual)
                self._record_response(result, actual, response)
                result.candidates.extend(self._response_candidates(paper, response, bindings))
                if response.content_type in ('text/html', 'application/xhtml+xml'):
                    self._linked_candidates(paper, response, result, visited, linked_budget)
            except (SourceError, ValueError, TypeError) as exc:
                self._record_failure(result, url, exc)
        if any(item.get('incomplete') for item in result.warnings):
            result.status = ProviderStatus.PARTIAL
        if result.errors:
            result.status = ProviderStatus.PARTIAL if result.candidates else ProviderStatus.ERROR
        return result

    def _probe(self, url):
        return self.client.probe(
            url,
            self.config.preview_bytes,
            self.config.html_metadata_bytes,
            self.config.maximum_redirects,
            allow_source_backpressure=True,
        )

    @staticmethod
    def _record_response(result, url, response):
        result.diagnostics.append(
            dict(response.request_context, url=url, context='public_resource', status=ProviderStatus.OK)
        )

    @staticmethod
    def _record_failure(result, url, error):
        kind = getattr(error, 'error_type', 'source_error')
        request_context = getattr(error, 'request_context', {})
        if kind == 'source_deferred':
            result.warnings.append({'code': kind, 'url': url, 'incomplete': True, 'request_context': request_context})
        else:
            result.errors.append(f'{url}: {error}')
        result.diagnostics.append(
            {
                'url': url,
                'status': ProviderStatus.DEFERRED if kind == 'source_deferred' else ProviderStatus.ERROR,
                'context': 'public_resource',
                'error_type': kind,
                'status_code': getattr(error, 'status_code', None),
                **request_context,
            }
        )

    def _linked_candidates(self, paper, response, result, visited, linked_budget):
        content = response.prefix.decode('utf-8', errors='replace')
        parser = PublicLinks()
        parser.feed(content)
        if not response.body_complete and len(response.prefix) >= self.config.html_metadata_bytes:
            result.warnings.append(
                {'code': 'page_read_limit', 'url': response.url, 'incomplete': True, 'bytes_read': len(response.prefix)}
            )
        citation = CitationMetadata()
        citation.feed(content)
        values = citation.values
        date = self._single(values, 'citation_publication_date') or self._single(values, 'citation_date')
        identity = metadata_identity(
            paper,
            self.name,
            doi=self._single(values, 'citation_doi'),
            title=self._single(values, 'citation_title'),
            year=date[:4] if date else None,
            authors=values.get('citation_author'),
        )
        for link in parser.links:
            alternate_pdf = link.get('type') == 'application/pdf' and 'alternate' in link.get('rel', '').split()
            title_matches = normalize(link['text'] or link['heading']) == normalize(paper.get('title') or '')
            if not alternate_pdf and not title_matches:
                continue
            try:
                target = secure_page_url(urljoin(response.url, link['href']))
            except (SourceError, ValueError) as exc:
                result.warnings.append(
                    {
                        'code': 'candidate_rejected',
                        'incomplete': False,
                        'reason': 'Unsafe linked resource was not queried: ' + str(exc),
                    }
                )
                continue
            if alternate_pdf:
                result.candidates.append(
                    make_candidate(
                        target,
                        self.name,
                        identity,
                        {'metadata_url': response.url, 'title': self._single(values, 'citation_title')},
                        'Scholarly page explicitly declares an alternate application/pdf resource',
                    )
                )
                continue
            if target in visited:
                continue
            if linked_budget['requests'] >= self.config.linked_page_limit:
                result.warnings.append(
                    {
                        'code': 'linked_page_limit',
                        'url': response.url,
                        'incomplete': True,
                        'outcome': RequestOutcome.QUERY_BUDGET_EXHAUSTED,
                    }
                )
                break
            visited.add(target)
            try:
                child = self._probe(target)
                self._record_response(result, target, child)
                if child.request_context.get('request_attempted', True):
                    linked_budget['requests'] += 1
                if child.content_type in ('text/html', 'application/xhtml+xml') and not child.body_complete:
                    result.warnings.append(
                        {
                            'code': 'page_read_limit',
                            'url': child.url,
                            'incomplete': True,
                            'bytes_read': len(child.prefix),
                        }
                    )
                found = self._response_candidates(paper, child, [])
                if not found and has_pdf_signature(child.prefix):
                    found.append(
                        make_candidate(
                            child.url,
                            self.name,
                            (
                                IdentityStatus.UNVERIFIED,
                                ['Bibliography link title alone cannot establish PDF identity'],
                            ),
                            {'metadata_url': response.url, 'link_text': link['text']},
                            'Explicit public bibliography link returned a PDF; correspondence requires confirmation',
                        )
                    )
                result.candidates.extend(found)
            except (SourceError, ValueError, TypeError) as exc:
                if getattr(exc, 'request_context', {}).get('request_attempted', True):
                    linked_budget['requests'] += 1
                self._record_failure(result, target, exc)
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
            identity = metadata_identity(
                paper, binding.get('database'), binding.get('source_paper_id'), binding.get('doi'), binding.get('title')
            )
            candidates.append(
                make_candidate(
                    response.url,
                    self.name,
                    identity,
                    dict(
                        binding,
                        metadata_url=binding.get('landing_page_url'),
                        anonymous_pdf_response=True,
                        indexed_is_oa=binding.get('is_oa'),
                    ),
                    'Supplied scholarly/institutional publication link returned an anonymous public '
                    'PDF; license is unknown',
                    binding.get('version'),
                    binding.get('license'),
                )
            )
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
            identity = metadata_identity(
                paper, self.name, title=item.get('headline') or item.get('name'), year=str(date)[:4], authors=names
            )
            media = item.get('encoding') or item.get('associatedMedia') or []
            if isinstance(media, dict):
                media = [media]
            for document in media:
                if not isinstance(document, dict) or document.get('encodingFormat') != 'application/pdf':
                    continue
                if document.get('contentUrl'):
                    candidates.append(
                        make_candidate(
                            urljoin(url, document['contentUrl']),
                            self.name,
                            identity,
                            {
                                'metadata_url': url,
                                'title': item.get('headline') or item.get('name'),
                                'metadata_year': str(date)[:4],
                                'metadata_authors': names,
                            },
                            'ScholarlyArticle structured metadata explicitly declares a PDF encoding',
                        )
                    )
        return candidates
