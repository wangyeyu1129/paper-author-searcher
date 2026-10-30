"""PDF locations already in the collected metadata (OpenAlex records the search fetched)."""

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import SourceError
from paper_author_searcher.models import PDFProviderResult
from paper_author_searcher.pdf_identity import source_records
from paper_author_searcher.source_traits import OPENALEX, SEMANTIC_SCHOLAR
from paper_author_searcher.status import ProviderStatus

from .openalex import openalex_candidates, openalex_landing_pages
from .semantic_scholar import semantic_candidates


def _openalex_locations(provider, result, paper, metadata):
    result.candidates.extend(openalex_candidates(paper, metadata, provider.name, config=provider.config))
    result.landing_pages.extend(openalex_landing_pages(metadata))


def _semantic_scholar_locations(provider, result, paper, metadata):
    result.candidates.extend(semantic_candidates(paper, metadata, provider.name, config=provider.config))


# Each source that saves PDF locations in its records, and how to read them.
LOCATION_READERS = {OPENALEX: _openalex_locations, SEMANTIC_SCHOLAR: _semantic_scholar_locations}


class ExistingPDFProvider:
    name = 'existing_metadata'

    def __init__(self, config=DEFAULT_PDF):
        self.config = config

    def find(self, paper):
        result = PDFProviderResult(self.name)
        for record in source_records(paper):
            metadata = record.get('raw_metadata') or {}
            reader = LOCATION_READERS.get(record.get('source'))
            if reader is None:
                continue
            try:
                reader(self, result, paper, metadata)
            except (SourceError, ValueError, TypeError) as exc:
                result.errors.append(str(exc))
        if result.errors:
            result.status = ProviderStatus.PARTIAL
        return result
