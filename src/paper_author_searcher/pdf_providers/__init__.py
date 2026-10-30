"""Independent public metadata providers; no paywall or login workarounds.

One module per source family; every provider answers find(paper) with a PDFProviderResult."""

from .base import VERSION_TYPES, MetadataPDFProvider, make_candidate
from .existing import ExistingPDFProvider
from .homepage import HomepagePDFProvider
from .landing_pages import CitationMetadata, LandingPagePDFProvider
from .open_access import EuropePMCPDFProvider, OpenAIREPDFProvider, UnpaywallPDFProvider
from .openalex import (
    OPENALEX_ARXIV_LOCATION,
    OpenAlexPDFProvider,
    openalex_arxiv_ids,
    openalex_authors,
    openalex_candidates,
    openalex_landing_pages,
    openalex_locations,
)
from .repositories import ArxivPDFProvider, NBERPDFProvider
from .semantic_scholar import SemanticScholarPDFProvider, semantic_candidates

__all__ = [
    'OPENALEX_ARXIV_LOCATION',
    'VERSION_TYPES',
    'ArxivPDFProvider',
    'CitationMetadata',
    'EuropePMCPDFProvider',
    'ExistingPDFProvider',
    'HomepagePDFProvider',
    'LandingPagePDFProvider',
    'MetadataPDFProvider',
    'NBERPDFProvider',
    'OpenAIREPDFProvider',
    'OpenAlexPDFProvider',
    'SemanticScholarPDFProvider',
    'UnpaywallPDFProvider',
    'make_candidate',
    'openalex_arxiv_ids',
    'openalex_authors',
    'openalex_candidates',
    'openalex_landing_pages',
    'openalex_locations',
    'semantic_candidates',
]
