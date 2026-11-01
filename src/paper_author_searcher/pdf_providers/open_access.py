"""Open-access aggregators: Unpaywall, Europe PMC and OpenAIRE."""

from typing import Any
from urllib.parse import quote, urlsplit

from paper_author_searcher.config import DEFAULT_PDF, DEFAULT_SOURCES
from paper_author_searcher.http import SourceError
from paper_author_searcher.models import PDFProviderResult
from paper_author_searcher.pdf_identity import metadata_identity, paper_dois
from paper_author_searcher.pdf_settings import redact_contact
from paper_author_searcher.settings import validate_contact_email
from paper_author_searcher.status import ProviderStatus

from .base import MetadataPDFProvider, make_candidate


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
    """Europe PMC's free full-text PDFs (PubMed Central copies), looked up by DOI, and NCBI's copy of each."""

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
            if work.get('pmcid') and self.config.pmc_open_data_url_template:
                provenance = {
                    'metadata_url': self.sources.europepmc_url,
                    'doi': work.get('doi'),
                    'metadata_title': work.get('title'),
                    'pmcid': work.get('pmcid'),
                    'site': 'PMC Open Access dataset',
                }
                candidates.append(
                    make_candidate(
                        self.config.pmc_open_data_url_template.format(pmcid=work['pmcid']),
                        self.name,
                        identity,
                        provenance,
                        "NCBI's PMC Open Access dataset copy of the PMCID Europe PMC gave for this DOI",
                        'publishedVersion',
                        work.get('license'),
                    )
                )
        return candidates, []


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
