"""Discover deposited full-text and landing links from Crossref metadata."""

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from .config import DEFAULT_PDF, PDFConfig
from .http import SourceError
from .json_types import JSONObject
from .models import PDFProviderResult
from .pdf_identity import metadata_identity, paper_dois
from .pdf_providers import MetadataPDFProvider, make_candidate
from .pdf_settings import redact_contact
from .settings import validate_contact_email
from .status import ProviderStatus


class CrossrefPDFProvider(MetadataPDFProvider):
    name = 'crossref'

    def __init__(self, client: Any, config: PDFConfig = DEFAULT_PDF, email: Optional[str] = None) -> None:
        super().__init__(client)
        self.config = config
        self.cache: Dict[str, Any] = {}
        self.configuration_error: Optional[str] = None
        try:
            self.email = validate_contact_email(email)
        except ValueError as exc:
            self.email = None
            self.configuration_error = str(exc)

    def lookups(self, paper: JSONObject) -> List[str]:
        identifiers = [doi[len('doi:') :] for doi in sorted(paper_dois(paper))]
        if not identifiers and paper.get('title'):
            identifiers.append('title:' + paper['title'])
        return identifiers

    def find(self, paper: JSONObject) -> PDFProviderResult:
        result = super().find(paper)
        not_indexed = result.diagnostics and all(item.get('status_code') == 404 for item in result.diagnostics)
        if not_indexed and paper.get('title'):
            try:
                candidates, pages = self.lookup(paper, 'title:' + paper['title'])
                result.candidates.extend(candidates)
                result.landing_pages.extend(pages)
                result.status = ProviderStatus.OK
                result.diagnostics.append({'lookup_method': 'title_search', 'status': ProviderStatus.OK})
            except (SourceError, ValueError, TypeError) as exc:
                result.status = ProviderStatus.ERROR
                result.errors.append('Title search: ' + str(exc))
                result.diagnostics.append(
                    {
                        'lookup_method': 'title_search',
                        'status': ProviderStatus.ERROR,
                        'status_code': getattr(exc, 'status_code', None),
                        'error_type': getattr(exc, 'error_type', 'malformed_response'),
                    }
                )
        if self.configuration_error:
            result.errors.append('Crossref optional contact configuration: ' + self.configuration_error)
        result.errors = [redact_contact(message, self.email) for message in result.errors]
        return result

    def lookup(self, paper: JSONObject, identifier: str) -> Tuple[List[Any], List[Any]]:
        url = self.config.crossref_url + '/works'
        params: Dict[str, Any] = {'mailto': self.email}
        search = identifier.startswith('title:')
        if search:
            params.update({'query.title': paper['title'], 'rows': self.config.crossref_search_results})
        else:
            url += '/' + quote(identifier, safe='')
        if identifier not in self.cache:
            self.cache[identifier] = self.client.get(url, params=params)
        response = self.cache[identifier]
        if not isinstance(response, dict) or response.get('status') != ProviderStatus.OK:
            raise SourceError('Crossref did not return an ok metadata response', error_type='malformed_response')
        message = response.get('message')
        if not isinstance(message, dict):
            raise SourceError('Crossref metadata message must be an object', error_type='malformed_response')
        works = message.get('items') if search else [message]
        if not isinstance(works, list) or any(not isinstance(work, dict) for work in works):
            raise SourceError('Crossref search metadata must contain a work list', error_type='malformed_response')
        candidates, pages = [], []
        for work in works:
            found, landing = self._locations(paper, work, url)
            candidates.extend(found)
            pages.extend(landing)
        return candidates, pages

    def _locations(self, paper: JSONObject, work: JSONObject, metadata_url: str) -> Tuple[List[Any], List[Any]]:
        titles = work.get('title') or []
        if not isinstance(titles, list) or any(not isinstance(title, str) for title in titles):
            raise SourceError('Crossref titles must be a list of strings', error_type='malformed_response')
        title = titles[0] if titles else None
        authors = []
        byline = work.get('author') or []
        if not isinstance(byline, list) or any(not isinstance(author, dict) for author in byline):
            raise SourceError('Crossref authors must be a list of objects', error_type='malformed_response')
        for author in byline:
            name = ' '.join(part for part in (author.get('given'), author.get('family')) if part)
            if name:
                authors.append(name)
        year = self._year(work)
        venues = work.get('container-title') or []
        if not isinstance(venues, list) or any(not isinstance(venue, str) for venue in venues):
            raise SourceError('Crossref container titles must be a list of strings', error_type='malformed_response')
        identity = metadata_identity(paper, self.name, doi=work.get('DOI'), title=title, year=year, authors=authors)
        metadata = {
            'database': self.name,
            'metadata_url': metadata_url,
            'doi': work.get('DOI'),
            'title': title,
            'metadata_year': year,
            'metadata_authors': authors,
            'metadata_venue': venues[0] if venues else None,
        }
        candidates = []
        for link in work.get('link') or []:
            if not isinstance(link, dict) or str(link.get('content-type') or '').lower() != 'application/pdf':
                continue
            if not link.get('URL'):
                continue
            version = {'vor': 'publishedVersion', 'am': 'acceptedVersion'}.get(str(link.get('content-version') or ''))
            candidates.append(
                make_candidate(
                    link['URL'],
                    self.name,
                    identity,
                    dict(metadata, intended_application=link.get('intended-application')),
                    'Crossref deposited full-text PDF link; anonymous public access still requires verification',
                    version,
                )
            )
        pages = []
        resource = work.get('resource') or {}
        if not isinstance(resource, dict) or not isinstance(resource.get('primary') or {}, dict):
            raise SourceError('Crossref resource metadata must contain objects', error_type='malformed_response')
        primary = resource.get('primary') or {}
        for url in (work.get('URL'), primary.get('URL')):
            if url:
                pages.append(dict(metadata, landing_page_url=url))
        return candidates, pages

    @staticmethod
    def _year(work: JSONObject) -> Optional[int]:
        for field in ('published', 'published-print', 'published-online', 'issued'):
            date = work.get(field) or {}
            if not isinstance(date, dict):
                continue
            parts = date.get('date-parts') or []
            if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
                return parts[0][0]
        return None
