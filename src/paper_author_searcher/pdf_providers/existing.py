"""PDF locations already in the collected metadata (OpenAlex records the search fetched)."""

from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import SourceError
from paper_author_searcher.models import PDFProviderResult
from paper_author_searcher.pdf_identity import source_records
from paper_author_searcher.status import ProviderStatus

from .openalex import openalex_candidates, openalex_landing_pages
from .semantic_scholar import semantic_candidates


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
