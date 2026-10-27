"""Built-in data sources, registered by name.

To add a source, write its adapter and register a factory here (or in a plugin module imported by the composition
root); enable it by listing its name in ``sources.identity_sources`` or ``pdf.sources``. No orchestrator changes.
Factories receive the ``ServiceContainer``, so they draw clients, settings and credentials from one place.
"""

from typing import Any

from .listed_works import (
    ArxivWorkLookup,
    AuthorMatcher,
    CrossrefWorkLookup,
    OpenAlexWorkLookup,
    SemanticScholarWorkLookup,
    WorkLookup,
)
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
from .web_search import BraveSearch, ExaSearch, TavilySearch, WebSearch

IDENTITY_SOURCES: Registry[Any] = Registry('identity source')
# Paper collectors are keyed by the identity source whose author IDs they page through.
PAPER_SOURCES: Registry[PaperProvider] = Registry('paper source')
PDF_SOURCES: Registry[Any] = Registry('PDF source')
# Sources a listed work missing from the collection is looked up in, in the order ListedWorksConfig.sources gives.
WORK_LOOKUPS: Registry[WorkLookup] = Registry('work lookup')
# Web search providers for homepage discovery (DiscoveryConfig.web_search); a factory returns None without its key.
WEB_SEARCH: Registry[WebSearch | None] = Registry('web search')


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


def _paper_provider(container: Any, kind: type) -> Any:
    provider = next((item for item in container.paper_providers if isinstance(item, kind)), None)
    if provider is None:
        raise ValueError(f'Work lookup needs the {kind.__name__}; enable its identity source')
    return provider


@WORK_LOOKUPS.register('openalex')
def _openalex_lookup(container: Any) -> OpenAlexWorkLookup:
    settings = container.settings
    papers = _paper_provider(container, OpenAlexPaperProvider)
    return OpenAlexWorkLookup(papers, settings.listed_works, AuthorMatcher(settings.matching))


@WORK_LOOKUPS.register('semantic_scholar')
def _semantic_scholar_lookup(container: Any) -> SemanticScholarWorkLookup:
    papers = _paper_provider(container, SemanticScholarPaperProvider)
    return SemanticScholarWorkLookup(papers, AuthorMatcher(container.settings.matching))


@WORK_LOOKUPS.register('crossref')
def _crossref_lookup(container: Any) -> CrossrefWorkLookup:
    settings = container.settings
    return CrossrefWorkLookup(
        container.http_client,
        settings.sources.crossref_url,
        settings.listed_works,
        AuthorMatcher(settings.matching),
        settings.credentials.crossref_email,
    )


@WORK_LOOKUPS.register('arxiv')
def _arxiv_lookup(container: Any) -> ArxivWorkLookup:
    settings = container.settings
    return ArxivWorkLookup(
        container.http_client, settings.sources.arxiv_url, settings.listed_works, AuthorMatcher(settings.matching)
    )


@WEB_SEARCH.register('brave')
def _brave(container: Any) -> BraveSearch | None:
    key = container.settings.credentials.brave_api_key
    return BraveSearch(container.http_client, container.settings.discovery.brave_url, key) if key else None


@WEB_SEARCH.register('tavily')
def _tavily(container: Any) -> TavilySearch | None:
    key = container.settings.credentials.tavily_api_key
    return TavilySearch(container.http_client, container.settings.discovery.tavily_url, key) if key else None


@WEB_SEARCH.register('exa')
def _exa(container: Any) -> ExaSearch | None:
    key = container.settings.credentials.exa_api_key
    return ExaSearch(container.http_client, container.settings.discovery.exa_url, key) if key else None
