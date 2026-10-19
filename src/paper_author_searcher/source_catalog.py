"""Built-in data sources, registered by name.

To add a source, write its adapter and register a factory here (or in a plugin module imported by the composition
root); enable it by listing its name in ``sources.identity_sources`` or ``pdf.sources``. No orchestrator changes.
Factories receive the ``ServiceContainer``, so they draw clients, settings and credentials from one place.
"""

from typing import Any

from .paper_providers import DBLPPaperProvider, OpenAlexPaperProvider, PaperProvider, SemanticScholarPaperProvider
from .pdf_catalogs import JournalCatalogPDFProvider
from .pdf_crossref import CrossrefPDFProvider
from .pdf_providers import (
    ArxivPDFProvider,
    ExistingPDFProvider,
    OpenAlexPDFProvider,
    SemanticScholarPDFProvider,
    UnpaywallPDFProvider,
)
from .pdf_public_pages import PublicPagePDFProvider
from .providers import DBLPProvider, OpenAlexProvider, SemanticScholarProvider
from .registry import Registry

IDENTITY_SOURCES: Registry[Any] = Registry('identity source')
# Paper collectors are keyed by the identity source whose author IDs they page through.
PAPER_SOURCES: Registry[PaperProvider] = Registry('paper source')
PDF_SOURCES: Registry[Any] = Registry('PDF source')


@IDENTITY_SOURCES.register('openalex')
def _openalex_identity(container: Any) -> OpenAlexProvider:
    settings = container.settings
    return OpenAlexProvider(
        client=container.http_client,
        api_key=settings.credentials.openalex_api_key,
        source_config=settings.sources,
        matching_config=settings.matching,
    )


@IDENTITY_SOURCES.register('semantic_scholar')
def _semantic_scholar_identity(container: Any) -> SemanticScholarProvider:
    settings = container.settings
    return SemanticScholarProvider(
        client=container.http_client,
        api_key=settings.credentials.semantic_scholar_api_key,
        source_config=settings.sources,
    )


@IDENTITY_SOURCES.register('dblp')
def _dblp_identity(container: Any) -> DBLPProvider:
    return DBLPProvider(client=container.http_client, source_config=container.settings.sources)


@PAPER_SOURCES.register('openalex')
def _openalex_papers(container: Any, identity: OpenAlexProvider) -> OpenAlexPaperProvider:
    return OpenAlexPaperProvider(identity, container.collection_config)


@PAPER_SOURCES.register('semantic_scholar')
def _semantic_scholar_papers(container: Any, identity: SemanticScholarProvider) -> SemanticScholarPaperProvider:
    return SemanticScholarPaperProvider(identity, container.collection_config)


@PAPER_SOURCES.register('dblp')
def _dblp_papers(container: Any, identity: DBLPProvider) -> DBLPPaperProvider:
    return DBLPPaperProvider(identity)


@PDF_SOURCES.register('existing_metadata')
def _existing(container: Any) -> ExistingPDFProvider:
    return ExistingPDFProvider()


@PDF_SOURCES.register('openalex')
def _openalex_pdf(container: Any) -> OpenAlexPDFProvider:
    settings = container.settings
    return OpenAlexPDFProvider(
        container.resource_requests, settings.sources, api_key=settings.credentials.openalex_api_key
    )


@PDF_SOURCES.register('semantic_scholar')
def _semantic_scholar_pdf(container: Any) -> SemanticScholarPDFProvider:
    settings = container.settings
    return SemanticScholarPDFProvider(
        container.resource_requests,
        settings.sources,
        api_key=settings.credentials.semantic_scholar_api_key,
        config=container.pdf_config,
    )


@PDF_SOURCES.register('crossref')
def _crossref_pdf(container: Any) -> CrossrefPDFProvider:
    return CrossrefPDFProvider(
        container.resource_requests, container.pdf_config, email=container.settings.credentials.crossref_email
    )


@PDF_SOURCES.register('unpaywall')
def _unpaywall_pdf(container: Any) -> UnpaywallPDFProvider:
    email = container.options.unpaywall_email or container.settings.credentials.unpaywall_email
    return UnpaywallPDFProvider(container.resource_requests, email, container.pdf_config)


@PDF_SOURCES.register('arxiv')
def _arxiv_pdf(container: Any) -> ArxivPDFProvider:
    return ArxivPDFProvider(container.resource_requests, container.pdf_config)


@PDF_SOURCES.register('journal_catalog')
def _journal_catalogs(container: Any) -> JournalCatalogPDFProvider:
    return JournalCatalogPDFProvider(container.resource_requests, container.pdf_config)


@PDF_SOURCES.register('public_pages')
def _public_pages(container: Any) -> PublicPagePDFProvider:
    return PublicPagePDFProvider(container.resource_requests, container.pdf_config)
