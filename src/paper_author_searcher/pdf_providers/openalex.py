"""OpenAlex work locations: PDF candidates, arXiv editions and landing pages."""

from urllib.parse import quote

from paper_author_searcher.config import DEFAULT_PDF, DEFAULT_SOURCES
from paper_author_searcher.http import SourceError
from paper_author_searcher.pdf_identity import arxiv_id, metadata_identity, paper_dois, paper_ids, source_records

from .base import MetadataPDFProvider, make_candidate

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
