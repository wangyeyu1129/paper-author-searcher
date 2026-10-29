"""Built-in data sources, registered by name.

To add a source, write its adapter and register a factory here (or in a plugin module imported by the composition
root); enable it by listing its name in ``sources.identity_sources`` or ``pdf.sources``. No orchestrator changes.
Factories receive the ``ServiceContainer``, so they draw clients, settings and credentials from one place.
"""

from typing import Any

from .cs_sources import ArxivAuthorSearch
from .dblp_dump import DblpDumpClient, DblpWorkLookup
from .listed_works import (
    ArxivWorkLookup,
    AuthorMatcher,
    CrossrefWorkLookup,
    OpenAlexWorkLookup,
    SemanticScholarWorkLookup,
    WorkLookup,
)
from .name_searches import (
    CoreWorkLookup,
    CrossrefAuthorSearch,
    EuropePmcAuthorSearch,
    EuropePmcWorkLookup,
)
from .paper_collector import NameSearch
from .paper_providers import DBLPPaperProvider, OpenAlexPaperProvider, PaperProvider, SemanticScholarPaperProvider
from .pdf_catalogs import JournalCatalogPDFProvider
from .pdf_crossref import CrossrefPDFProvider
from .pdf_providers import (
    ArxivPDFProvider,
    EuropePMCPDFProvider,
    ExistingPDFProvider,
    HomepagePDFProvider,
    NBERPDFProvider,
    OpenAIREPDFProvider,
    OpenAlexPDFProvider,
    SemanticScholarPDFProvider,
    UnpaywallPDFProvider,
)
from .pdf_public_pages import PublicPagePDFProvider
from .precision_guards import DepartmentMatcher
from .providers import DBLPProvider, OpenAlexProvider, SemanticScholarProvider
from .registry import Registry
from .venue_catalogs import CatalogWorkLookup, RepositoryNameSearch
from .web_search import BraveSearch, ExaSearch, TavilySearch, WebSearch

IDENTITY_SOURCES: Registry[Any] = Registry('identity source')
# Paper collectors are keyed by the identity source whose author IDs they page through.
PAPER_SOURCES: Registry[PaperProvider] = Registry('paper source')
PDF_SOURCES: Registry[Any] = Registry('PDF source')
# Sources a listed work missing from the collection is looked up in, in the order ListedWorksConfig.sources gives.
WORK_LOOKUPS: Registry[WorkLookup | None] = Registry('work lookup')
# Sources searched by the person's name after author-ID collection (NameSearchConfig.sources); None when disabled.
NAME_SEARCHES: Registry[NameSearch | None] = Registry('name search')
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
        matching_config=settings.matching,
    )


@IDENTITY_SOURCES.register('dblp')
def _dblp_identity(container: Any) -> DBLPProvider:
    """Reads the DBLP dump index when one is built; otherwise the DBLP API (often refused to automated clients)."""
    index = container.dblp_index
    client = DblpDumpClient(index, container.settings.sources) if index is not None else container.http_client
    return DBLPProvider(
        client=client,
        source_config=container.settings.sources,
        matching_config=container.settings.matching,
        departments=DepartmentMatcher(container.settings.precision),
    )


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
    return ExistingPDFProvider(container.pdf_config)


@PDF_SOURCES.register('openalex')
def _openalex_pdf(container: Any) -> OpenAlexPDFProvider:
    settings = container.settings
    return OpenAlexPDFProvider(
        container.resource_requests,
        settings.sources,
        api_key=settings.credentials.openalex_api_key,
        config=container.pdf_config,
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


@PDF_SOURCES.register('europepmc')
def _europepmc_pdf(container: Any) -> EuropePMCPDFProvider:
    return EuropePMCPDFProvider(container.resource_requests, container.settings.sources, container.pdf_config)


@PDF_SOURCES.register('openaire')
def _openaire_pdf(container: Any) -> OpenAIREPDFProvider:
    return OpenAIREPDFProvider(container.resource_requests, container.pdf_config)


@PDF_SOURCES.register('nber')
def _nber_pdf(container: Any) -> NBERPDFProvider:
    return NBERPDFProvider(container.resource_requests, container.pdf_config)


@PDF_SOURCES.register('arxiv')
def _arxiv_pdf(container: Any) -> ArxivPDFProvider:
    return ArxivPDFProvider(container.resource_requests, container.pdf_config)


@PDF_SOURCES.register('journal_catalog')
def _journal_catalogs(container: Any) -> JournalCatalogPDFProvider:
    return JournalCatalogPDFProvider(container.resource_requests, container.pdf_config)


@PDF_SOURCES.register('public_pages')
def _public_pages(container: Any) -> PublicPagePDFProvider:
    return PublicPagePDFProvider(container.resource_requests, container.pdf_config)


@PDF_SOURCES.register('homepage')
def _homepage_pdf(container: Any) -> HomepagePDFProvider:
    return HomepagePDFProvider(container.pdf_config)


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


@WORK_LOOKUPS.register('europepmc')
def _europepmc_lookup(container: Any) -> EuropePmcWorkLookup:
    settings = container.settings
    return EuropePmcWorkLookup(
        container.http_client, settings.sources.europepmc_url, settings.listed_works, AuthorMatcher(settings.matching)
    )


@WORK_LOOKUPS.register('core')
def _core_lookup(container: Any) -> CoreWorkLookup | None:
    settings = container.settings
    key = settings.credentials.core_api_key
    if not key:
        return None
    return CoreWorkLookup(
        container.http_client, settings.sources.core_url, key, settings.listed_works, AuthorMatcher(settings.matching)
    )


@NAME_SEARCHES.register('arxiv')
def _arxiv_name_search(container: Any) -> ArxivAuthorSearch | None:
    settings = container.settings
    if not settings.cs_sources.arxiv_author_search:
        return None
    return ArxivAuthorSearch(container.http_client, settings.sources.arxiv_url, settings.cs_sources, settings.matching)


@NAME_SEARCHES.register('europepmc')
def _europepmc_name_search(container: Any) -> EuropePmcAuthorSearch:
    settings = container.settings
    return EuropePmcAuthorSearch(
        container.http_client, settings.sources.europepmc_url, settings.name_searches, settings.matching
    )


@NAME_SEARCHES.register('crossref')
def _crossref_name_search(container: Any) -> CrossrefAuthorSearch:
    settings = container.settings
    return CrossrefAuthorSearch(
        container.http_client,
        settings.sources.crossref_url,
        settings.name_searches,
        settings.matching,
        settings.credentials.crossref_email,
    )


@WORK_LOOKUPS.register('dblp')
def _dblp_lookup(container: Any) -> DblpWorkLookup | None:
    index = container.dblp_index
    if index is None:
        return None
    settings = container.settings
    return DblpWorkLookup(index, AuthorMatcher(settings.matching), settings.sources)


@WORK_LOOKUPS.register('catalogs')
def _catalog_lookup(container: Any) -> CatalogWorkLookup | None:
    store = container.catalog_store
    return CatalogWorkLookup(store, AuthorMatcher(container.settings.matching)) if store is not None else None


@NAME_SEARCHES.register('repositories')
def _repository_search(container: Any) -> RepositoryNameSearch | None:
    store = container.catalog_store
    if store is None or not container.settings.catalogs.oai_repositories:
        return None
    return RepositoryNameSearch(store, container.settings.matching)
