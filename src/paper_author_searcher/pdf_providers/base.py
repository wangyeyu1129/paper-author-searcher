"""Shared candidate construction and the metadata-provider template."""

from abc import ABC, abstractmethod
from http import HTTPStatus
from typing import Any, ClassVar

from paper_author_searcher.config import DEFAULT_SOURCES
from paper_author_searcher.http import SourceError
from paper_author_searcher.models import PDFCandidate, PDFProviderResult
from paper_author_searcher.status import ProviderStatus

VERSION_TYPES = {
    'publishedVersion': 'published_version',
    'acceptedVersion': 'accepted_manuscript',
    'submittedVersion': 'submitted_manuscript',
    'published_version': 'published_version',
    'accepted_manuscript': 'accepted_manuscript',
    'submitted_manuscript': 'submitted_manuscript',
}


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
                elif getattr(exc, 'status_code', None) != HTTPStatus.NOT_FOUND:
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
        if result.diagnostics and all(item.get('status_code') == HTTPStatus.NOT_FOUND for item in result.diagnostics):
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
