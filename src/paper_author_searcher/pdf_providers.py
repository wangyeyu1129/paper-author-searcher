"""Independent public metadata providers; no paywall or login workarounds."""
import os
import time
from html.parser import HTMLParser
from urllib.parse import quote, urljoin

from .config import DEFAULT_PDF, DEFAULT_SOURCES
from .http import HttpClient, SourceError
from .matching import normalize
from .models import PDFCandidate, PDFProviderResult
from .pdf_identity import (
    arxiv_id, arxiv_ids, metadata_identity, paper_dois, paper_ids,
    same_arxiv_version, source_records,
)


VERSION_TYPES = {
    'publishedVersion': 'published_version',
    'acceptedVersion': 'accepted_manuscript',
    'submittedVersion': 'submitted_manuscript',
    'published_version': 'published_version',
    'accepted_manuscript': 'accepted_manuscript',
    'submitted_manuscript': 'submitted_manuscript',
}


def openalex_locations(work):
    if not isinstance(work, dict):
        raise SourceError('Malformed work metadata: expected an object')
    locations = work.get('locations') or []
    if not isinstance(locations, list):
        raise SourceError('Malformed OpenAlex locations: expected a list')
    locations = list(locations)
    for key in ('primary_location', 'best_oa_location'):
        if work.get(key):
            locations.append(work[key])
    if not all(isinstance(location, dict) for location in locations):
        raise SourceError('Malformed OpenAlex location: expected an object')
    return locations


def make_candidate(url, source, identity, metadata, access_basis, version=None, license=None):
    if not isinstance(url, str):
        raise SourceError('Malformed PDF URL: expected a string')
    version_type = VERSION_TYPES.get(version, 'unknown')
    return PDFCandidate(
        url=url, source=source, identity_status=identity[0], identity_evidence=identity[1],
        access_basis=access_basis, version_type=version_type,
        license=license, provenance=[dict(metadata, provider=source, version_type=version_type)],
    )


def openalex_candidates(paper, work, provider, metadata_url=None):
    locations = openalex_locations(work)
    identity = metadata_identity(
        paper, 'openalex', work.get('id'), work.get('doi'), work.get('title'),
        work.get('publication_year'),
    )
    candidates = []
    for location in locations:
        if location.get('is_oa') is not True or not location.get('pdf_url'):
            continue
        provenance = {
            'database': 'openalex', 'source_paper_id': work.get('id'),
            'doi': work.get('doi'), 'metadata_url': metadata_url,
            'metadata_title': work.get('title'), 'metadata_year': work.get('publication_year'),
            'location_id': location.get('id'), 'is_oa': location.get('is_oa'),
            'landing_page_url': location.get('landing_page_url'),
            'version': location.get('version'), 'license': location.get('license'),
        }
        candidates.append(make_candidate(
            location['pdf_url'], provider, identity, provenance,
            'OpenAlex location explicitly marked is_oa=true',
            location.get('version'), location.get('license'),
        ))
    return candidates


def semantic_candidates(paper, work, provider, metadata_url=None):
    location = work.get('openAccessPdf') or {}
    if not isinstance(location, dict):
        raise SourceError('Malformed Semantic Scholar openAccessPdf: expected an object')
    if not location.get('url') or str(location.get('status', '')).upper() == 'CLOSED':
        return []
    external_ids = work.get('externalIds') or {}
    identity = metadata_identity(
        paper, 'semantic_scholar', work.get('paperId'), external_ids.get('DOI'),
        work.get('title'), work.get('year'),
        [author.get('name', '') for author in work.get('authors') or []],
    )
    provenance = {
        'database': 'semantic_scholar', 'source_paper_id': work.get('paperId'),
        'doi': external_ids.get('DOI'), 'metadata_url': metadata_url,
        'metadata_title': work.get('title'), 'metadata_year': work.get('year'),
        'oa_status': location.get('status'), 'license': location.get('license'),
    }
    return [make_candidate(location['url'], provider, identity, provenance,
                           'Semantic Scholar openAccessPdf metadata', license=location.get('license'))]


def openalex_landing_pages(work):
    pages = []
    for location in openalex_locations(work):
        if location.get('landing_page_url'):
            pages.append(dict(location, database='openalex', source_paper_id=work.get('id'),
                              title=work.get('title'), doi=work.get('doi')))
    return pages


class ExistingPDFProvider:
    name = 'existing_metadata'

    def find(self, paper):
        result = PDFProviderResult(self.name)
        for record in source_records(paper):
            metadata = record.get('raw_metadata') or {}
            try:
                if record.get('source') == 'openalex':
                    result.candidates.extend(openalex_candidates(paper, metadata, self.name))
                    result.landing_pages.extend(openalex_landing_pages(metadata))
                elif record.get('source') == 'semantic_scholar':
                    result.candidates.extend(semantic_candidates(paper, metadata, self.name))
            except (SourceError, ValueError, TypeError) as exc:
                result.errors.append(str(exc))
        if result.errors:
            result.status = 'partial'
        return result


class MetadataPDFProvider:
    """Small shared loop: each metadata lookup fails independently."""
    def __init__(self, client=None, sources=DEFAULT_SOURCES):
        self.client = client or HttpClient()
        self.sources = sources

    def find(self, paper):
        result = PDFProviderResult(self.name)
        lookups = self.lookups(paper)
        if not lookups:
            result.status = 'not_applicable'
            return result
        for identifier in lookups:
            try:
                candidates, landing_pages = self.lookup(paper, identifier)
                result.candidates.extend(candidates)
                result.landing_pages.extend(landing_pages)
            except (SourceError, ValueError, TypeError) as exc:
                result.errors.append('{}: {}'.format(identifier, exc))
        if result.errors:
            result.status = 'partial' if len(result.errors) < len(lookups) else 'error'
        return result


class OpenAlexPDFProvider(MetadataPDFProvider):
    name = 'openalex'
    fields = 'id,doi,title,publication_year,primary_location,best_oa_location,locations'

    def __init__(self, client=None, sources=DEFAULT_SOURCES, api_key=None):
        super().__init__(client, sources)
        self.api_key = api_key or os.getenv('OPENALEX_API_KEY')

    def lookups(self, paper):
        identifiers = paper_ids(paper, self.name)
        if identifiers:
            return identifiers
        return ['https://doi.org/' + doi[len('doi:'):] for doi in sorted(paper_dois(paper))]

    def lookup(self, paper, identifier):
        url = self.sources.openalex_url + '/works/' + quote(identifier, safe=':/')
        for record in source_records(paper):
            work = record.get('raw_metadata') or {}
            if record.get('source') != self.name or 'locations' not in work:
                continue
            if not isinstance(work['locations'], list):
                continue
            if str(work.get('id', '')).rstrip('/').rsplit('/', 1)[-1] != str(identifier).rstrip('/').rsplit('/', 1)[-1]:
                continue
            return openalex_candidates(paper, work, self.name, 'saved_openalex_metadata'), openalex_landing_pages(work)
        work = self.client.get(url, params={'select': self.fields, 'api_key': self.api_key})
        return openalex_candidates(paper, work, self.name, url), openalex_landing_pages(work)


class SemanticScholarPDFProvider(MetadataPDFProvider):
    name = 'semantic_scholar'
    fields = 'title,year,authors,externalIds,openAccessPdf'

    def __init__(self, client=None, sources=DEFAULT_SOURCES, api_key=None):
        super().__init__(client, sources)
        self.api_key = api_key or os.getenv('SEMANTIC_SCHOLAR_API_KEY')

    def lookups(self, paper):
        identifiers = paper_ids(paper, self.name)
        if identifiers:
            return identifiers
        return ['DOI:' + doi[len('doi:'):] for doi in sorted(paper_dois(paper))]

    def lookup(self, paper, identifier):
        url = self.sources.semantic_scholar_url + '/paper/' + quote(identifier, safe=':')
        for record in source_records(paper):
            work = record.get('raw_metadata') or {}
            if record.get('source') != self.name or 'openAccessPdf' not in work:
                continue
            if work.get('paperId') == identifier:
                return semantic_candidates(paper, work, self.name, 'saved_semantic_scholar_metadata'), []
        headers = {'x-api-key': self.api_key} if self.api_key else None
        work = self.client.get(url, params={'fields': self.fields}, headers=headers)
        return semantic_candidates(paper, work, self.name, url), []


class UnpaywallPDFProvider(MetadataPDFProvider):
    name = 'unpaywall'

    def __init__(self, client=None, email=None, config=DEFAULT_PDF):
        super().__init__(client)
        self.email = email or os.getenv('UNPAYWALL_EMAIL')
        self.config = config

    def find(self, paper):
        if not self.email:
            return PDFProviderResult(self.name, status='disabled',
                                     errors=['Unpaywall requires a real contact email'])
        return super().find(paper)

    def lookups(self, paper):
        return [doi[len('doi:'):] for doi in sorted(paper_dois(paper))]

    def lookup(self, paper, identifier):
        url = self.config.unpaywall_url + '/' + quote(identifier, safe='/')
        work = self.client.get(url, params={'email': self.email})
        identity = metadata_identity(paper, self.name, doi=work.get('doi'), title=work.get('title'))
        locations = list(work.get('oa_locations') or [])
        if work.get('best_oa_location'):
            locations.append(work['best_oa_location'])
        candidates = []
        if work.get('is_oa') is not True:
            return candidates, []
        for location in locations:
            if not location.get('url_for_pdf'):
                continue
            provenance = {'metadata_url': url, 'doi': work.get('doi'), 'metadata_title': work.get('title'),
                          'host_type': location.get('host_type'), 'version': location.get('version')}
            candidates.append(make_candidate(
                location['url_for_pdf'], self.name, identity, provenance,
                'Unpaywall OA location url_for_pdf', location.get('version'), location.get('license'),
            ))
        return candidates, []


class ArxivPDFProvider:
    name = 'arxiv'
    atom = '{http://www.w3.org/2005/Atom}'
    arxiv = '{http://arxiv.org/schemas/atom}'

    def __init__(self, client=None, config=DEFAULT_PDF):
        self.client = client or HttpClient()
        self.config = config
        self.last_request = None

    def find(self, paper):
        identifiers = arxiv_ids(paper)
        search = not identifiers and self.config.arxiv_title_search and bool(paper.get('title'))
        if not identifiers and not search:
            return PDFProviderResult(self.name, status='not_applicable')
        result = PDFProviderResult(self.name)
        try:
            self._wait_for_interval()
            params = {'id_list': ','.join(identifiers)}
            if search:
                title = ' '.join(paper['title'].replace('"', ' ').split())
                params = {'search_query': 'ti:"' + title + '"', 'max_results': self.config.arxiv_search_results}
            feed = self.client.get(self.config.arxiv_url, params=params, xml=True)
            entries = feed.findall(self.atom + 'entry')
            if not entries and not search:
                raise SourceError('arXiv returned no matching entries')
            for entry in entries:
                if search and normalize(entry.findtext(self.atom + 'title') or '') != normalize(paper['title']):
                    continue
                result.candidates.extend(self._entry_candidates(paper, identifiers, entry, search))
        except (SourceError, ValueError, TypeError) as exc:
            result.status = 'error'
            result.errors.append(str(exc))
        return result

    def _wait_for_interval(self):
        if self.last_request is not None:
            remaining = self.config.arxiv_request_interval - (time.monotonic() - self.last_request)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request = time.monotonic()

    def _entry_candidates(self, paper, requested, entry, search=False):
        identifier = arxiv_id(entry.findtext(self.atom + 'id'))
        if not identifier or (not search and not any(same_arxiv_version(value, identifier) for value in requested)):
            raise SourceError('arXiv entry does not match the requested identifier/version')
        title = ' '.join((entry.findtext(self.atom + 'title') or '').split())
        doi = entry.findtext(self.arxiv + 'doi')
        # A supplied arXiv ID identifies a version even when the journal DOI is absent.
        bound_paper = dict(paper, records=list(source_records(paper)) + [
            {'source': 'arxiv', 'source_paper_id': identifier},
        ])
        published = entry.findtext(self.atom + 'published') or ''
        authors = [author.findtext(self.atom + 'name') or '' for author in entry.findall(self.atom + 'author')]
        identity = metadata_identity(paper if search else bound_paper, self.name, identifier, doi, title,
                                     published[:4], authors)
        candidates = []
        for link in entry.findall(self.atom + 'link'):
            if link.get('type') != 'application/pdf' or not link.get('href'):
                continue
            candidate = make_candidate(
                link.get('href'), self.name, identity,
                {'source_paper_id': identifier, 'metadata_url': self.config.arxiv_url,
                 'doi': doi, 'metadata_title': title, 'requested_ids': requested}, 'arXiv API PDF link',
            )
            candidate.version_type = 'preprint'
            candidate.provenance[0]['version_type'] = 'preprint'
            candidates.append(candidate)
        return candidates


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


class LandingPagePDFProvider:
    """Read explicit scholarly citation_pdf_url, never arbitrary page hyperlinks."""
    name = 'landing_page'

    def __init__(self, client=None, config=DEFAULT_PDF):
        self.client = client or HttpClient()
        self.config = config

    def find(self, paper):
        result = PDFProviderResult(self.name)
        pages = self._pages(paper)
        urls = list(pages)
        if not urls:
            result.status = 'not_applicable'
            return result
        for url in urls:
            try:
                result.candidates.extend(self._lookup(paper, url, pages[url]))
            except (SourceError, ValueError, TypeError) as exc:
                result.errors.append('{}: {}'.format(url, exc))
        if result.errors:
            result.status = 'partial' if len(result.errors) < len(urls) else 'error'
        return result

    def _pages(self, paper):
        pages = {}
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
        response = self.client.probe(url, self.config.preview_bytes,
                                     self.config.html_metadata_bytes, self.config.maximum_redirects)
        if not 200 <= response.status < 300:
            raise SourceError('Landing page HTTP {}'.format(response.status))
        if response.content_type not in ('text/html', 'application/xhtml+xml'):
            if response.prefix.lstrip().startswith(b'%PDF-'):
                return self._direct_pdf(paper, response, bindings)
            return []
        parser = CitationMetadata()
        parser.feed(response.prefix.decode('utf-8', errors='replace'))
        values = parser.values
        pdf_urls = values.get('citation_pdf_url', [])
        if not pdf_urls:
            return []
        title = self._single(values, 'citation_title')
        doi = self._single(values, 'citation_doi')
        date = self._single(values, 'citation_publication_date') or self._single(values, 'citation_date')
        year = date[:4] if date and date[:4].isdigit() else None
        identity = metadata_identity(paper, self.name, doi=doi, title=title, year=year,
                                     authors=values.get('citation_author'))
        for key in ('citation_title', 'citation_doi'):
            if len(set(values.get(key, []))) > 1:
                identity = ('unverified', ['Landing page contains multiple paper identities'])
        candidates = []
        for pdf_url in pdf_urls:
            candidates.append(make_candidate(
                urljoin(response.url, pdf_url), self.name, identity,
                {'metadata_url': response.url, 'doi': doi, 'title': title,
                 'metadata_year': year, 'metadata_authors': values.get('citation_author', []),
                 'metadata_bytes_read': len(response.prefix)},
                'Public scholarly page explicitly supplies citation_pdf_url',
            ))
        return candidates

    def _direct_pdf(self, paper, response, bindings):
        candidates = []
        for binding in bindings:
            if binding.get('is_oa') is not True:
                continue
            identity = metadata_identity(paper, binding['database'], binding.get('source_paper_id'),
                                         binding.get('doi'), binding.get('title'))
            candidates.append(make_candidate(
                response.url, self.name, identity,
                dict(binding, metadata_url=binding['landing_page_url']),
                'OpenAlex OA landing location returned an actual PDF signature',
                binding.get('version'), binding.get('license'),
            ))
        return candidates

    def _single(self, values, key):
        items = values.get(key) or []
        return items[0] if len(set(items)) == 1 else None
