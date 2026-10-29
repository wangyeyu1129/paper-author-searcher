"""Independent public metadata providers; no paywall or login workarounds."""

import re
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import quote, urljoin, urlsplit

from .config import DEFAULT_PDF, DEFAULT_SOURCES
from .html_support import decode_response
from .http import SourceError
from .matching import normalize
from .models import PDFCandidate, PDFProviderResult
from .pacing import RequestPacer
from .pdf_identity import (
    arxiv_id,
    arxiv_ids,
    metadata_identity,
    paper_dois,
    paper_ids,
    related_dois,
    same_arxiv_version,
    source_records,
)
from .pdf_settings import redact_contact
from .pdf_source_requests import SemanticScholarRequests
from .settings import validate_contact_email
from .status import IdentityStatus, ProviderStatus

VERSION_TYPES = {
    'publishedVersion': 'published_version',
    'acceptedVersion': 'accepted_manuscript',
    'submittedVersion': 'submitted_manuscript',
    'published_version': 'published_version',
    'accepted_manuscript': 'accepted_manuscript',
    'submitted_manuscript': 'submitted_manuscript',
}


# OpenAlex names an arXiv copy's location after the arXiv OAI record it harvested.
OPENALEX_ARXIV_LOCATION = 'pmh:oai:arxiv.org:'


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
        url=url,
        source=source,
        identity_status=identity[0],
        identity_evidence=identity[1],
        access_basis=access_basis,
        version_type=version_type,
        license=license,
        provenance=[dict(metadata, provider=source, version_type=version_type)],
    )


def openalex_authors(work):
    """Author names of a saved OpenAlex work; a lookup that did not select authorships has none."""
    names = []
    for authorship in work.get('authorships') or []:
        name = ((authorship or {}).get('author') or {}).get('display_name') if isinstance(authorship, dict) else None
        if name:
            names.append(name)
    return names


def openalex_arxiv_ids(locations):
    """arXiv IDs of the copies OpenAlex merged into one work ("pmh:oai:arXiv.org:2404.18021", arxiv.org URLs)."""
    identifiers: list[str] = []
    for location in locations:
        values = [location.get('landing_page_url'), location.get('pdf_url')]
        source_id = str(location.get('id') or '')
        if source_id.lower().startswith(OPENALEX_ARXIV_LOCATION):
            values.append(source_id[len(OPENALEX_ARXIV_LOCATION) :])
        identifiers.extend(identifier for identifier in map(arxiv_id, values) if identifier)
    return list(dict.fromkeys(identifiers))


def openalex_candidates(paper, work, provider, metadata_url=None, config=DEFAULT_PDF):
    locations = openalex_locations(work)
    identity = metadata_identity(
        paper,
        'openalex',
        work.get('id'),
        work.get('doi'),
        work.get('title'),
        work.get('publication_year'),
        openalex_authors(work),
        openalex_arxiv_ids(locations),
        config,
    )
    candidates = []
    for location in locations:
        if location.get('is_oa') is not True or not location.get('pdf_url'):
            continue
        provenance = {
            'database': 'openalex',
            'source_paper_id': work.get('id'),
            'doi': work.get('doi'),
            'metadata_url': metadata_url,
            'metadata_title': work.get('title'),
            'metadata_year': work.get('publication_year'),
            'location_id': location.get('id'),
            'is_oa': location.get('is_oa'),
            'landing_page_url': location.get('landing_page_url'),
            'version': location.get('version'),
            'license': location.get('license'),
        }
        candidates.append(
            make_candidate(
                location['pdf_url'],
                provider,
                identity,
                provenance,
                'OpenAlex location explicitly marked is_oa=true',
                location.get('version'),
                location.get('license'),
            )
        )
    return candidates


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


def openalex_landing_pages(work):
    pages = []
    for location in openalex_locations(work):
        urls = [location.get('landing_page_url')]
        if location.get('is_oa') is not True:
            urls.append(location.get('pdf_url'))
        for url in dict.fromkeys(value for value in urls if value):
            pages.append(
                dict(
                    location,
                    database='openalex',
                    source_paper_id=work.get('id'),
                    title=work.get('title'),
                    doi=work.get('doi'),
                    landing_page_url=url,
                    supplied_location=dict(location),
                )
            )
    return pages


class ExistingPDFProvider:
    name = 'existing_metadata'

    def __init__(self, config=DEFAULT_PDF):
        self.config = config

    def find(self, paper):
        result = PDFProviderResult(self.name)
        for record in source_records(paper):
            metadata = record.get('raw_metadata') or {}
            try:
                if record.get('source') == 'openalex':
                    result.candidates.extend(openalex_candidates(paper, metadata, self.name, config=self.config))
                    result.landing_pages.extend(openalex_landing_pages(metadata))
                elif record.get('source') == 'semantic_scholar':
                    result.candidates.extend(semantic_candidates(paper, metadata, self.name, config=self.config))
            except (SourceError, ValueError, TypeError) as exc:
                result.errors.append(str(exc))
        if result.errors:
            result.status = ProviderStatus.PARTIAL
        return result


class MetadataPDFProvider(ABC):
    """Template method for metadata APIs: each identifier lookup fails independently of the others."""

    name: ClassVar[str]

    @abstractmethod
    def lookups(self, paper: dict) -> list[str]:
        """Identifiers this source can be queried with for ``paper`` (empty: not applicable)."""

    @abstractmethod
    def lookup(self, paper: dict, identifier: str) -> tuple[list[Any], list[Any]]:
        """Return ``(pdf_candidates, landing_pages)`` for one identifier."""

    def __init__(self, client, sources=DEFAULT_SOURCES):
        self.client = client
        self.sources = sources

    def find(self, paper):
        result = PDFProviderResult(self.name)
        lookups = self.lookups(paper)
        if not lookups:
            result.status = ProviderStatus.NOT_APPLICABLE
            return result
        for identifier in lookups:
            try:
                candidates, landing_pages = self.lookup(paper, identifier)
                result.candidates.extend(candidates)
                result.landing_pages.extend(landing_pages)
                result.diagnostics.append({'identifier': identifier, 'status': ProviderStatus.OK})
            except (SourceError, ValueError, TypeError) as exc:
                if getattr(exc, 'error_type', None) == 'source_deferred':
                    result.warnings.append({'code': 'source_deferred', 'incomplete': True, 'identifier': identifier})
                elif getattr(exc, 'status_code', None) != 404:
                    result.errors.append(f'{identifier}: {exc}')
                result.diagnostics.append(
                    {
                        'identifier': identifier,
                        'status': ProviderStatus.ERROR,
                        'status_code': getattr(exc, 'status_code', None),
                        'error_type': getattr(exc, 'error_type', 'malformed_response'),
                        'retry_after': getattr(exc, 'retry_after', None),
                        'request_context': getattr(exc, 'request_context', {}),
                    }
                )
        if result.diagnostics and all(item.get('status_code') == 404 for item in result.diagnostics):
            result.status = ProviderStatus.NOT_FOUND
        if result.warnings:
            result.status = ProviderStatus.PARTIAL
        if result.errors:
            failures = [
                item
                for item in result.diagnostics
                if item.get('status') == ProviderStatus.ERROR and item.get('error_type') != 'not_found'
            ]
            if failures:
                succeeded = any(item.get('status') == ProviderStatus.OK for item in result.diagnostics)
                result.status = ProviderStatus.PARTIAL if succeeded else ProviderStatus.ERROR
            else:
                result.status = (
                    ProviderStatus.OK if result.candidates or result.landing_pages else ProviderStatus.NOT_FOUND
                )
        return result


class OpenAlexPDFProvider(MetadataPDFProvider):
    name = 'openalex'
    fields = 'id,doi,title,publication_year,primary_location,best_oa_location,locations'

    def __init__(self, client, sources=DEFAULT_SOURCES, api_key=None, config=DEFAULT_PDF):
        super().__init__(client, sources)
        self.api_key = api_key
        self.config = config

    def lookups(self, paper):
        identifiers = paper_ids(paper, self.name)
        if identifiers:
            return identifiers
        return ['https://doi.org/' + doi[len('doi:') :] for doi in sorted(paper_dois(paper))]

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
            return openalex_candidates(
                paper, work, self.name, 'saved_openalex_metadata', self.config
            ), openalex_landing_pages(work)
        work = self.client.get(url, params={'select': self.fields, 'api_key': self.api_key})
        return openalex_candidates(paper, work, self.name, url, self.config), openalex_landing_pages(work)


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
                if normalize(work.get('title') or '') == normalize(paper['title']):
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


class UnpaywallPDFProvider(MetadataPDFProvider):
    name = 'unpaywall'

    def __init__(self, client, email=None, config=DEFAULT_PDF):
        super().__init__(client)
        self.config = config
        self.configuration_error = None
        try:
            self.email = validate_contact_email(email)
        except ValueError as exc:
            self.email = None
            self.configuration_error = str(exc)

    def find(self, paper):
        if self.configuration_error:
            return PDFProviderResult(self.name, status=ProviderStatus.DISABLED, errors=[self.configuration_error])
        if not self.email:
            return PDFProviderResult(
                self.name, status=ProviderStatus.DISABLED, errors=['Unpaywall requires a real contact email']
            )
        result = super().find(paper)
        result.errors = [redact_contact(message, self.email) for message in result.errors]
        return result

    def lookups(self, paper):
        return [doi[len('doi:') :] for doi in sorted(paper_dois(paper))]

    def lookup(self, paper, identifier):
        url = self.config.unpaywall_url + '/' + quote(identifier, safe='/')
        work = self.client.get(url, params={'email': self.email})
        if not isinstance(work, dict):
            raise SourceError('Unpaywall metadata must be an object', error_type='malformed_response')
        identity = metadata_identity(paper, self.name, doi=work.get('doi'), title=work.get('title'), config=self.config)
        locations = work.get('oa_locations') or []
        if not isinstance(locations, list):
            raise SourceError('Unpaywall OA locations must be a list', error_type='malformed_response')
        locations = list(locations)
        if work.get('best_oa_location'):
            locations.append(work['best_oa_location'])
        if any(not isinstance(location, dict) for location in locations):
            raise SourceError('Unpaywall OA locations must contain objects', error_type='malformed_response')
        candidates: list[Any] = []
        landing_pages = []
        if work.get('is_oa') is not True:
            return candidates, []
        for location in locations:
            if location.get('url_for_landing_page'):
                landing_pages.append(
                    dict(
                        location,
                        landing_page_url=location['url_for_landing_page'],
                        database=self.name,
                        doi=work.get('doi'),
                        title=work.get('title'),
                        is_oa=True,
                    )
                )
            if not location.get('url_for_pdf'):
                continue
            provenance = {
                'metadata_url': url,
                'doi': work.get('doi'),
                'metadata_title': work.get('title'),
                'host_type': location.get('host_type'),
                'version': location.get('version'),
            }
            candidates.append(
                make_candidate(
                    location['url_for_pdf'],
                    self.name,
                    identity,
                    provenance,
                    'Unpaywall OA location url_for_pdf',
                    location.get('version'),
                    location.get('license'),
                )
            )
        return candidates, landing_pages


class EuropePMCPDFProvider(MetadataPDFProvider):
    """Europe PMC's own free full-text PDFs (PubMed Central copies), looked up by DOI."""

    name = 'europepmc'

    def __init__(self, client, sources=DEFAULT_SOURCES, config=DEFAULT_PDF):
        super().__init__(client, sources)
        self.config = config

    def lookups(self, paper):
        return [doi[len('doi:') :] for doi in sorted(paper_dois(paper))]

    def lookup(self, paper, identifier):
        data = self.client.get(
            self.sources.europepmc_url,
            params={'query': f'DOI:"{identifier}"', 'format': 'json', 'resultType': 'core', 'pageSize': 1},
        )
        results = ((data or {}).get('resultList') or {}).get('result') or []
        if not isinstance(results, list):
            raise SourceError('Europe PMC results must be a list', error_type='malformed_response')
        candidates: list[Any] = []
        for work in results:
            if not isinstance(work, dict):
                raise SourceError('Europe PMC results must contain objects', error_type='malformed_response')
            identity = metadata_identity(
                paper, self.name, doi=work.get('doi'), title=work.get('title'), config=self.config
            )
            links = (work.get('fullTextUrlList') or {}).get('fullTextUrl') or []
            for link in links if isinstance(links, list) else []:
                if not isinstance(link, dict) or link.get('documentStyle') != 'pdf' or not link.get('url'):
                    continue
                if link.get('availabilityCode') not in self.config.europepmc_free_codes:
                    continue
                provenance = {
                    'metadata_url': self.sources.europepmc_url,
                    'doi': work.get('doi'),
                    'metadata_title': work.get('title'),
                    'pmcid': work.get('pmcid'),
                    'site': link.get('site'),
                    'availability': link.get('availability'),
                }
                candidates.append(
                    make_candidate(
                        link['url'],
                        self.name,
                        identity,
                        provenance,
                        f"Europe PMC full text marked {link.get('availability')}",
                        'publishedVersion',
                        work.get('license'),
                    )
                )
        return candidates, []


class NBERPDFProvider(MetadataPDFProvider):
    """NBER working-paper PDFs, named by the NBER DOI of the paper or of an edition related to it.

    Deduplication relates an edition only on the same normalized title and compatible authors, so the working
    paper of a later journal article is bound to it; the downloaded file must still show the title.
    """

    name = 'nber'

    def __init__(self, client, config=DEFAULT_PDF):
        super().__init__(client)
        self.config = config

    def lookups(self, paper):
        own = [doi[len('doi:') :] for doi in sorted(paper_dois(paper))]
        numbers = [self._number(doi) for doi in [*own, *related_dois(paper)]]
        return list(dict.fromkeys(number for number in numbers if number))

    def lookup(self, paper, identifier):
        own = {self._number(doi[len('doi:') :]) for doi in paper_dois(paper)}
        if identifier in own:
            identity = (IdentityStatus.VERIFIED, ['Exact DOI agreement with saved paper metadata'])
        else:
            identity = (
                IdentityStatus.VERIFIED,
                ['NBER DOI of an edition deduplication related to this paper (same normalized title and authors)'],
            )
        url = self.config.nber_pdf_url_template.format(number=identifier)
        provenance = {'doi': '10.3386/' + identifier, 'source_paper_id': identifier}
        candidate = make_candidate(
            url, self.name, identity, provenance, 'NBER working papers are free to download', 'submittedVersion'
        )
        return [candidate], []

    def _number(self, doi):
        match = re.match(self.config.nber_doi_pattern, doi or '', flags=re.IGNORECASE)
        return match.group(1).lower() if match else None


class OpenAIREPDFProvider(MetadataPDFProvider):
    """Repository copies the OpenAIRE Graph aggregates for an open-access work, looked up by DOI.

    A URL to a PDF file is a candidate; any other URL is a landing page for the sources after this one.
    """

    name = 'openaire'

    def __init__(self, client, config=DEFAULT_PDF):
        super().__init__(client)
        self.config = config

    def lookups(self, paper):
        return [doi[len('doi:') :] for doi in sorted(paper_dois(paper))]

    def lookup(self, paper, identifier):
        data = self.client.get(self.config.openaire_url, params={'pid': identifier})
        results = (data or {}).get('results') or []
        if not isinstance(results, list):
            raise SourceError('OpenAIRE results must be a list', error_type='malformed_response')
        candidates: list[Any] = []
        landing_pages: list[Any] = []
        for work in results:
            if not isinstance(work, dict):
                raise SourceError('OpenAIRE results must contain objects', error_type='malformed_response')
            if (work.get('bestAccessRight') or {}).get('label') not in self.config.openaire_open_labels:
                continue
            identity = metadata_identity(
                paper, self.name, doi=identifier, title=work.get('mainTitle'), config=self.config
            )
            for instance in work.get('instances') or []:
                for url in (instance or {}).get('urls') or []:
                    if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
                        continue
                    if urlsplit(url).path.lower().endswith('.pdf'):
                        provenance = {
                            'metadata_url': self.config.openaire_url,
                            'doi': identifier,
                            'metadata_title': work.get('mainTitle'),
                        }
                        candidates.append(
                            make_candidate(
                                url,
                                self.name,
                                identity,
                                provenance,
                                'OpenAIRE open-access repository copy',
                                None,
                                (instance.get('license') or None),
                            )
                        )
                    else:
                        landing_pages.append(
                            {
                                'landing_page_url': url,
                                'database': self.name,
                                'doi': identifier,
                                'title': work.get('mainTitle'),
                                'is_oa': True,
                            }
                        )
        return candidates, landing_pages


class ArxivPDFProvider:
    name = 'arxiv'
    atom = '{http://www.w3.org/2005/Atom}'
    arxiv = '{http://arxiv.org/schemas/atom}'

    def __init__(self, client, config=DEFAULT_PDF):
        self.client = client
        self.config = config
        self.pacer = RequestPacer(config.arxiv_request_interval)

    def find(self, paper):
        identifiers = arxiv_ids(paper)
        search = not identifiers and self.config.arxiv_title_search and bool(paper.get('title'))
        if not identifiers and not search:
            return PDFProviderResult(self.name, status=ProviderStatus.NOT_APPLICABLE)
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
            result.status = ProviderStatus.ERROR
            result.errors.append(str(exc))
        return result

    def _wait_for_interval(self):
        self.pacer.wait()

    def _entry_candidates(self, paper, requested, entry, search=False):
        identifier = arxiv_id(entry.findtext(self.atom + 'id'))
        if not identifier or (not search and not any(same_arxiv_version(value, identifier) for value in requested)):
            raise SourceError('arXiv entry does not match the requested identifier/version')
        title = ' '.join((entry.findtext(self.atom + 'title') or '').split())
        doi = entry.findtext(self.arxiv + 'doi')
        # A supplied arXiv ID identifies a version even when the journal DOI is absent.
        bound_paper = dict(
            paper,
            records=[*list(source_records(paper)), {'source': 'arxiv', 'source_paper_id': identifier}],
        )
        published = entry.findtext(self.atom + 'published') or ''
        authors = [author.findtext(self.atom + 'name') or '' for author in entry.findall(self.atom + 'author')]
        identity = metadata_identity(
            paper if search else bound_paper,
            self.name,
            identifier,
            doi,
            title,
            published[:4],
            authors,
            config=self.config,
        )
        candidates = []
        for link in entry.findall(self.atom + 'link'):
            if link.get('type') != 'application/pdf' or not link.get('href'):
                continue
            candidate = make_candidate(
                link.get('href'),
                self.name,
                identity,
                {
                    'source_paper_id': identifier,
                    'metadata_url': self.config.arxiv_url,
                    'doi': doi,
                    'metadata_title': title,
                    'metadata_year': published[:4],
                    'metadata_authors': authors,
                    'requested_ids': requested,
                },
                'arXiv API PDF link',
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
